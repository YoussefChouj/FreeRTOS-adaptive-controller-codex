// ------------------------------------------------------------------------------
// MRAC Implementation File
// ------------------------------------------------------------------------------
// Contains the core adaptive control logic for the 4 axes of a free-flying
// quadcopter (Pitch, Roll, Yaw, Z-Axis).
//
// Owner:   Stabilizer_Task, 200 Hz (MRAC_DT): MRAC_Control once per tick from TASK/StabilizerTask.c
//          Compute_Motor, after the PID loops; MRAC_Init once at boot (Controller_Init).
// Inputs:  Ctrler (PID FB/Des/U per loop), imu_data (simplex envelope), mrac_flags / mrac_config_* /
//          mrac_simplex / mrac_inj (ground-station commands), mrac_in_armed / mrac_in_phase.
// Outputs: mrac_state.<axis>.u_ad (the correction API/controller.c adds to u_nom), Theta / Whatf weights,
//          mrac_bus, mrac_var_id, mrac_cyc (cycle counts), mrac_simplex.fade.
// Tunables: MRAC_SET / MRAC_BASIS tables in MRAC_Init, MRAC_VAR_FIELD bounds (CMD 0x1D).
// Proof of a refactor: python API/tests/run_mrac_equiv.py (bit-exact) and python tools/fw_trace.py mrac.
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

#define MRAC_BW_MIN    0.1f     // rad/s, floor on ref_model_bw where it divides (P = 1/(2 bw), a0, a1)
#define MRAC_ZETA_MIN  0.1f     // floor on ref_model_zeta (a1 = 2 zeta wn divides)
#define MRAC_FADE_S    0.1f     // simplex: time for the u_ad fade to go 1 -> 0 or back, s
#define MRAC_SAT_FRAC  0.999f   // simplex: |u_ad| at or above this fraction of u_max counts as saturated
#define MRAC_DEG2RAD   0.0174533f   // PID FB/Des (deg/s) and imu_data.pit/rol (deg) -> the rad(/s) MRAC works in
#define MRAC_FINITE(v) ((v) - (v) == 0.0f)  // false for NaN and +-Inf (the control path's finite test)

// Global instance of the MRAC runtime states
MRAC_State_t mrac_state MRAC_CCM;
MRAC_FeatureFlags_t mrac_flags = {0};

// Global configurations for the 6 axes
MRAC_AxisConfig_t mrac_config_pitch MRAC_CCM;
MRAC_AxisConfig_t mrac_config_roll MRAC_CCM;
MRAC_AxisConfig_t mrac_config_yaw MRAC_CCM;
MRAC_AxisConfig_t mrac_config_z MRAC_CCM;
MRAC_AxisConfig_t mrac_config_x MRAC_CCM;
MRAC_AxisConfig_t mrac_config_y MRAC_CCM;

/* Per-axis lookups, indexed by MRAC_Axis_e (AXES entries). */
static MRAC_AxisConfig_t *const mrac_cfg_of[AXES] = {
    &mrac_config_pitch, &mrac_config_roll, &mrac_config_yaw, &mrac_config_z, &mrac_config_x, &mrac_config_y
};
static MRAC_AxisState_t *const mrac_st_of[AXES] = {
    &mrac_state.pitch, &mrac_state.roll, &mrac_state.yaw, &mrac_state.z_rate, &mrac_state.x_vel, &mrac_state.y_vel
};

/* Simplex fallback (MRAC_SimplexStep, CMD 0x19). Defaults are inert: mode 0, variant 0, fade 1. */
MRAC_Simplex_t mrac_simplex = {
    0, 0, 0, 0,          /* mode (0 off), variant (0 PID+MRAC), tripped, reason */
    0, 0,                /* trip_count, would_trip_count */
    {0, 0, 0, 0}, 0,     /* sat_ticks[4], clear_ticks */
    200,                 /* hold_ticks: 1 s at 200 Hz back inside the envelope before resuming */
    40,                  /* sat_ticks_max: 200 ms of |u_ad| at u_max trips (reason 4) */
    3.14f, 3.14f,        /* roll_max, pitch_max, rad (pi: never) */
    1.0e6f,              /* w_norm_max, ||Theta||_2 per axis (1e6: never) */
    1.0f                 /* fade, u_ad multiplier (1 = full) */
};

MRAC_Inj_t mrac_inj = {0};
volatile uint8_t mrac_in_armed = 0;
volatile uint8_t mrac_in_phase = 0;
volatile float mrac_in_vbat = 0.0f;
volatile float mrac_in_acc[2] = {0.0f, 0.0f};
volatile float mrac_xy_unom[2] = {0.0f, 0.0f};   /* x/y nominal accel cm/s^2 before u_ad (Pos_Compute, 100 Hz) */

/* D3 (spec 2026-10-22 item 4, docs/research/2026-10-22-d3-mrac-wrap.md): the x/y reference model per nominal
 * kind. MRAC_XyRefSelect applies a row to x and y when the operator's selection (g_xy_ctrl_kind, latched on the
 * ground) changes; CMD 0x13 / 0x1D writes made after it stay until the next selection. The ADRC / LQR / MPC rows
 * copy the PID row: PROPOSED, nothing measured. In the research sim, MRAC on the PID-demand reference made ADRC
 * worse (1.70 -> 3.12 cm rms hold, gamma 1): fill those rows from the sim sweep before flying them.
 *   @type   -      [0, 2]    reference model type (1 = first order: passthrough would learn the nominal lag)
 *   @bw     rad/s  [0.5, 100]  bandwidth (wn on type 2). PID: measured, first-order fit of FB to Des, 5 figure-8 logs
 *   @zeta   -      [0.1, 2]  damping (type 2 only)
 *   @ticks  MRAC_DT [0, 7]   pure delay on r before the reference model (ref_delay_s = ticks * MRAC_DT)
 */
#define MRAC_XY_REF_ROW(type, bw, zeta, ticks) { (type), (bw), (zeta), (uint8_t)(ticks) }
const MRAC_XyRefRow_t mrac_xy_ref_rows[MRAC_XY_REF_N] = {
    /*               type  bw    zeta  ticks */
    MRAC_XY_REF_ROW( 1.0f, 2.0f, 0.8f, 0),   /* XY_CTRL_PID  0: the boot values */
    MRAC_XY_REF_ROW( 1.0f, 2.0f, 0.8f, 0),   /* XY_CTRL_ADRC 1: PROPOSED = PID row */
    MRAC_XY_REF_ROW( 1.0f, 2.0f, 0.8f, 0),   /* XY_CTRL_LQR  2: PROPOSED = PID row */
    MRAC_XY_REF_ROW( 1.0f, 2.0f, 0.8f, 0),   /* XY_CTRL_MPC  3: PROPOSED = PID row */
};
volatile uint8_t mrac_xy_ref_kind = 0U;

/* Gate W (spec 2026-10-24 item 0): an explicit CMD 0x1D write of a reference field wins over the row default.
 * The two bytes below say which fields of the x (byte 0) and of the y (byte 1) reference the operator has written
 * in the current ground window, so the row selection (which runs on the ground latch) fills only the fields still
 * at their default. A write that comes after the selection and a write that comes before it in the same ground
 * window both survive. The record is per axis and per field: a write on x never stops the row from filling y.
 * The ground window ends at the disarm edge (MRAC_XyRefWindowEnd), so a reference fitted for one flight does not
 * leak into the next one.
 *   MRAC_XY_REF_W_TYPE  ref_type        MRAC_XY_REF_W_BW   ref_model_bw
 *   MRAC_XY_REF_W_ZETA  ref_model_zeta  MRAC_XY_REF_W_DELAY ref_delay_s */
#define MRAC_XY_REF_W_TYPE  0x01U
#define MRAC_XY_REF_W_BW    0x02U
#define MRAC_XY_REF_W_ZETA  0x04U
#define MRAC_XY_REF_W_DELAY 0x08U
#define MRAC_XY_REF_W_X     0U    /* the index of the x axis in the record below */
#define MRAC_XY_REF_W_Y     1U    /* the index of the y axis in the record below */
static volatile uint8_t mrac_xy_ref_written[2] = {0U, 0U};

/* The ground window is over (the drone landed): drop the record of the explicit writes and arm the row to apply
 * again, so the next flight starts from the nominal row unless the operator writes a new reference in its own
 * ground window. MRAC_XyRefSelect (the ground latch) then fills the row. */
static void MRAC_XyRefWindowEnd(void)
{
    mrac_xy_ref_written[MRAC_XY_REF_W_X] = 0U;
    mrac_xy_ref_written[MRAC_XY_REF_W_Y] = 0U;
    mrac_xy_ref_kind = 0xFFU;    /* forces the apply: the next MRAC_XyRefSelect fills the row again */
}

void MRAC_XyRefSelect(uint8_t kind)
{
    const MRAC_XyRefRow_t *row;
    if (kind >= MRAC_XY_REF_N || kind == mrac_xy_ref_kind) return;
    row = &mrac_xy_ref_rows[kind];
    /* Per axis: a field written on one axis is not written on the other one. */
    if ((mrac_xy_ref_written[MRAC_XY_REF_W_X] & MRAC_XY_REF_W_TYPE) == 0U) mrac_config_x.ref_type = row->type;
    if ((mrac_xy_ref_written[MRAC_XY_REF_W_Y] & MRAC_XY_REF_W_TYPE) == 0U) mrac_config_y.ref_type = row->type;
    if ((mrac_xy_ref_written[MRAC_XY_REF_W_X] & MRAC_XY_REF_W_BW) == 0U) mrac_config_x.ref_model_bw = row->bw;
    if ((mrac_xy_ref_written[MRAC_XY_REF_W_Y] & MRAC_XY_REF_W_BW) == 0U) mrac_config_y.ref_model_bw = row->bw;
    if ((mrac_xy_ref_written[MRAC_XY_REF_W_X] & MRAC_XY_REF_W_ZETA) == 0U) mrac_config_x.ref_model_zeta = row->zeta;
    if ((mrac_xy_ref_written[MRAC_XY_REF_W_Y] & MRAC_XY_REF_W_ZETA) == 0U) mrac_config_y.ref_model_zeta = row->zeta;
    if ((mrac_xy_ref_written[MRAC_XY_REF_W_X] & MRAC_XY_REF_W_DELAY) == 0U)
        mrac_config_x.ref_delay_s = (float)row->delay_ticks * MRAC_DT;
    if ((mrac_xy_ref_written[MRAC_XY_REF_W_Y] & MRAC_XY_REF_W_DELAY) == 0U)
        mrac_config_y.ref_delay_s = (float)row->delay_ticks * MRAC_DT;
    mrac_xy_ref_kind = kind;
}

/* D4 (spec 2026-10-22 item 5, docs/research/2026-10-22-d4-outer-inner.md): the outer x/y position layer.
 * Reference model p_m'' = bw^2 (p_d - p_m) - 2 zeta bw p_m'; e = p - p_m; regressor phi = (1, p, vd);
 * law W' = gamma (phi e - sigma |e| W), |W_k| <= w_max; u = -W.phi, |u| <= u_max m/s, added to the velocity
 * demand (PID path only). Off at boot (mrac_xy_layers = INNER); CMD 0x1D field 31 sets the target, latched on
 * the ground (MRAC_XyLayersLatch). gamma / sigma / w_max / u_max are the research sim's (gamma 1: sine 27.8 ->
 * 17.8 cm, hold worse); the sim's reference was a noise-free PID twin, so bw / zeta are PROPOSED: rerun the sim
 * on this model before flying it.
 *   @gamma  -      [0, 20]    adaptation gain
 *   @sigma  -      [0, 1]     e-modification leak
 *   @w_max  -      [0, 10]    clamp per weight
 *   @u_max  m/s    [0, 1]     output clamp
 *   @bw     rad/s  [0.1, 10]  reference model natural frequency
 *   @zeta   -      [0.1, 2]   reference model damping
 */
#define MRAC_POS_ROW(gamma, sigma, w_max, u_max, bw, zeta) { (gamma), (sigma), (w_max), (u_max), (bw), (zeta) }
MRAC_PosConfig_t mrac_pos_cfg = MRAC_POS_ROW(1.0f, 0.1f, 2.0f, 0.3f, 1.0f, 1.0f);
volatile uint8_t mrac_xy_layers = MRAC_XY_LAYER_INNER;
volatile uint8_t mrac_xy_layers_live = MRAC_XY_LAYER_INNER;
volatile uint8_t mrac_att_layers = MRAC_LAYER_INNER;       /* CMD 0x1D field 33 target */
volatile uint8_t mrac_att_layers_live = MRAC_LAYER_INNER;
volatile uint8_t mrac_yaw_layers = MRAC_LAYER_INNER;       /* CMD 0x1D field 34 target */
volatile uint8_t mrac_yaw_layers_live = MRAC_LAYER_INNER;
volatile uint8_t mrac_z_layers = MRAC_LAYER_INNER;         /* CMD 0x1D field 35 target */
volatile uint8_t mrac_z_layers_live = MRAC_LAYER_INNER;
MRAC_PosLayer_t mrac_pos[2];

static void MRAC_PosReset(MRAC_PosLayer_t *s)
{
    s->W[0] = 0.0f;
    s->W[1] = 0.0f;
    s->W[2] = 0.0f;
    s->p_m = 0.0f;
    s->v_m = 0.0f;
    s->e = 0.0f;
    s->u = 0.0f;
}

void MRAC_XyLayersLatch(void)
{
    mrac_xy_layers_live = (uint8_t)((unsigned)mrac_xy_layers & (MRAC_XY_LAYER_INNER | MRAC_XY_LAYER_OUTER));
    MRAC_PosReset(&mrac_pos[0]);
    MRAC_PosReset(&mrac_pos[1]);
}

/* Ground only (next to MRAC_XyLayersLatch): the three new loop groups fly the selection that was on the pad.
 * Only the INNER / OUTER bits survive; 0 = off. The outer layers of items 2-4 read the _live values. */
void MRAC_GroupLayersLatch(void)
{
    const unsigned m = (unsigned)(MRAC_LAYER_INNER | MRAC_LAYER_OUTER);
    mrac_att_layers_live = (uint8_t)((unsigned)mrac_att_layers & m);
    mrac_yaw_layers_live = (uint8_t)((unsigned)mrac_yaw_layers & m);
    mrac_z_layers_live = (uint8_t)((unsigned)mrac_z_layers & m);
}

/* One Pos_Compute tick of axis i. on = 0 (hold, xy_ctrl flying, not airborne) or outer not live: the model
 * tracks the plant, W frozen, u = 0. A non-finite input resets the axis. */
float MRAC_PosStep(uint8_t i, uint8_t on, float p_d, float p, float v, float vd)
{
    const MRAC_PosConfig_t *c = &mrac_pos_cfg;
    MRAC_PosLayer_t *s;
    float a, u, w, phi[3];
    int k;

    if (i > 1U) return 0.0f;
    s = &mrac_pos[i];
    if (!(p_d - p_d == 0.0f) || !(p - p == 0.0f) || !(v - v == 0.0f) || !(vd - vd == 0.0f)) {
        MRAC_PosReset(s);
        return 0.0f;
    }
    if (!on || ((unsigned)mrac_xy_layers_live & MRAC_XY_LAYER_OUTER) == 0U) {
        s->p_m = p;
        s->v_m = v;
        s->e = 0.0f;
        s->u = 0.0f;
        return 0.0f;
    }
    a = c->bw * c->bw * (p_d - s->p_m) - 2.0f * c->zeta * c->bw * s->v_m;
    s->p_m += s->v_m * MRAC_POS_DT;
    s->v_m += a * MRAC_POS_DT;
    s->e = p - s->p_m;
    phi[0] = 1.0f;
    phi[1] = p;
    phi[2] = vd;
    u = 0.0f;
    for (k = 0; k < 3; k++) {
        w = s->W[k] + MRAC_POS_DT * c->gamma * (phi[k] * s->e - c->sigma * fabsf(s->e) * s->W[k]);
        if (w > c->w_max) w = c->w_max;
        else if (w < -c->w_max) w = -c->w_max;
        s->W[k] = w;
        u -= w * phi[k];
    }
    if (u > c->u_max) u = c->u_max;
    else if (u < -c->u_max) u = -c->u_max;
    s->u = u;
    return u;
}
volatile float mrac_in_h = 0.0f;                  /* Z_posPID.FB, m (ToF height), for the Z features */
volatile uint8_t mrac_z_feat_mask = 0;            /* Z feature bits, MRAC_ZFEAT_*; 0 = off (VAR_WRITE id 54) */
MRAC_Vp12_t mrac_vp12 = {
    0.5f, 0.3f, 2.5f,       /* anf_f0, anf_fmin, anf_fmax Hz (exp12 swing 0.44-0.88 Hz, mostly 0.49) */
    0.3f,                   /* anf_zeta */
    0.05f,                  /* anf_amin m/s^2 */
    2.0f,                   /* anf_gamma rad/s^2 */
    0.5f,                   /* swing_ref m/s^2: ~1 N on the drone at a 10 deg swing of 570 g (physics, not measured) */
    0.2f,                   /* sag_ref */
    0.0505f,                /* lag_tau s = 1/19.8 (roll sysid) */
    {{16777215.0f, 16777215.0f}, {16777215.0f, 16777215.0f}, {16777215.0f, 16777215.0f}, {16777215.0f, 16777215.0f}},
    {{16777215.0f, 16777215.0f}, {16777215.0f, 16777215.0f}, {16777215.0f, 16777215.0f}, {16777215.0f, 16777215.0f}},
    {0.5f, 0.5f},
    0.0f
};
uint16_t mrac_var_id[AXES];
int8_t mrac_ref_type_eff[AXES];

/* ST log barrier (sim/bench ctrl_p2_yucelen.py:86-93 log_bar_deriv): on above ALPHA*st_eps, SMOOTH caps 1/rem. */
#define MRAC_ST_BAR_ALPHA  0.75f
#define MRAC_ST_BAR_SMOOTH 0.02f

static void MRAC_VariantSnap(MRAC_AxisState_t *st);
static void MRAC_L2Snap(MRAC_AxisState_t *st);
#if MRAC_VARIANT == MRAC_VARIANT_MULTI
static void MRAC_Vp12Step(uint8_t hold, uint8_t arm_edge);
#endif

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
            MRAC_XyRefWindowEnd();  // Gate W: the ground window is over, the next flight starts from the row
        }
    }
    /* L3 prior written while disarmed (CMD 0x2F..0x34 sets the flag): re-seed here, in the control task, so the
     * GS task never touches Theta and the next flight starts from the new prior (warm axes). */
    if ((mrac_prior_reseed != 0U) && !mrac_in_armed) {
        mrac_prior_reseed = 0U;
        MRAC_ResetWeights();
    }
#if MRAC_VARIANT == MRAC_VARIANT_MULTI
    MRAC_Vp12Step((uint8_t)(mrac_flags.output_injection_on && is_flying),
                  (uint8_t)(!mrac_inj.prev_armed && mrac_in_armed));
#endif
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
// Always built since the L3 prior (spec 2026-10-22 item 3). sigma_prior = 0 (the default) skips every prior term,
// so the update is bit-identical to the build without them (run_mrac_equiv, plain). MRAC_ENABLE_SIGMA_PRIOR now only
// adds the sigma-prior scenario to the parity driver. With sigma_prior > 0 the gradient update in MRAC_UpdateAxis
// pulls Theta toward Theta_prior on the axes whose bit is set in mrac_prior_pull, and MRAC_ResetWeights seeds
// Theta = Whatf = Theta_prior (clamped) on the axes whose bit is set in mrac_prior_warm. Written by CMD 0x2F..0x34
// while disarmed (send_data.c Cmd_MracPrior).
//
// Axis-indexed storage: Theta_prior[MRAC_AXIS_*][:MAX_NUM_BASIS]. Mask bit = MRAC_Axis_e.
// ---------------------------------------------------------------------------
float Theta_prior[AXES][MAX_NUM_BASIS];
float sigma_prior = 0.0f;
uint8_t mrac_prior_pull = MRAC_PRIOR_ALL_AXES;
uint8_t mrac_prior_warm = 0U;
volatile uint8_t mrac_prior_reseed = 0U;

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
#elif MRAC_VARIANT == MRAC_VARIANT_MULTI
    /* ext_<slot>: meaning depends on cfg->basis, see MRAC_GenExt */
   ,{6, "ext_0", MRAC_BLK_RBF, MRAC_GRP_RBF}
   ,{7, "ext_1", MRAC_BLK_RBF, MRAC_GRP_RBF}
   ,{8, "ext_2", MRAC_BLK_RBF, MRAC_GRP_RBF}
   ,{9, "ext_3", MRAC_BLK_RBF, MRAC_GRP_RBF}
   ,{10, "ext_4", MRAC_BLK_RBF, MRAC_GRP_RBF}
   ,{11, "ext_5", MRAC_BLK_RBF, MRAC_GRP_RBF}
   ,{12, "ext_6", MRAC_BLK_RBF, MRAC_GRP_RBF}
   ,{13, "ext_7", MRAC_BLK_RBF, MRAC_GRP_RBF}
   ,{14, "ext_8", MRAC_BLK_RBF, MRAC_GRP_RBF}
   ,{15, "ext_9", MRAC_BLK_RBF, MRAC_GRP_RBF}
   ,{16, "ext_10", MRAC_BLK_RBF, MRAC_GRP_RBF}
   ,{17, "ext_11", MRAC_BLK_RBF, MRAC_GRP_RBF}
   ,{18, "ext_12", MRAC_BLK_RBF, MRAC_GRP_RBF}
   ,{19, "ext_13", MRAC_BLK_RBF, MRAC_GRP_RBF}
   ,{20, "ext_14", MRAC_BLK_RBF, MRAC_GRP_RBF}
   ,{21, "ext_15", MRAC_BLK_RBF, MRAC_GRP_RBF}
   ,{22, "ext_16", MRAC_BLK_RBF, MRAC_GRP_RBF}
   ,{23, "ext_17", MRAC_BLK_RBF, MRAC_GRP_RBF}
   ,{24, "ext_18", MRAC_BLK_RBF, MRAC_GRP_RBF}
   ,{25, "ext_19", MRAC_BLK_RBF, MRAC_GRP_RBF}
   ,{26, "ext_20", MRAC_BLK_RBF, MRAC_GRP_RBF}
   ,{27, "ext_21", MRAC_BLK_RBF, MRAC_GRP_RBF}
   ,{28, "ext_22", MRAC_BLK_RBF, MRAC_GRP_RBF}
   ,{29, "ext_23", MRAC_BLK_RBF, MRAC_GRP_RBF}
   ,{30, "ext_24", MRAC_BLK_RBF, MRAC_GRP_RBF}
   ,{31, "ext_25", MRAC_BLK_RBF, MRAC_GRP_RBF}
   ,{32, "ext_26", MRAC_BLK_RBF, MRAC_GRP_RBF}
   ,{33, "ext_27", MRAC_BLK_RBF, MRAC_GRP_RBF}
   ,{34, "ext_28", MRAC_BLK_RBF, MRAC_GRP_RBF}
   ,{35, "ext_29", MRAC_BLK_RBF, MRAC_GRP_RBF}
   ,{36, "ext_30", MRAC_BLK_RBF, MRAC_GRP_RBF}
   ,{37, "ext_31", MRAC_BLK_RBF, MRAC_GRP_RBF}
   ,{38, "ext_32", MRAC_BLK_RBF, MRAC_GRP_RBF}
   ,{39, "ext_33", MRAC_BLK_RBF, MRAC_GRP_RBF}
   ,{40, "ext_34", MRAC_BLK_RBF, MRAC_GRP_RBF}
   ,{41, "ext_35", MRAC_BLK_RBF, MRAC_GRP_RBF}
   ,{42, "ext_36", MRAC_BLK_RBF, MRAC_GRP_PHY}
   ,{43, "ext_37", MRAC_BLK_RBF, MRAC_GRP_PHY}
   ,{44, "ext_38", MRAC_BLK_RBF, MRAC_GRP_PHY}
   ,{45, "ext_39", MRAC_BLK_RBF, MRAC_GRP_PHY}
#endif
};
const uint8_t mrac_n_features = MRAC_N_FEATURES;

#if MRAC_VARIANT == MRAC_VARIANT_MULTI
/* FW-B runtime feature set of an axis (MRAC_BASIS_*); yaw and z are always S6 */
static int MRAC_BasisOf(MRAC_Axis_e axis)
{
    float b = 0.0f;
    if (axis == MRAC_AXIS_PITCH) b = mrac_config_pitch.basis;
    else if (axis == MRAC_AXIS_ROLL) b = mrac_config_roll.basis;
    else if (axis == MRAC_AXIS_Z) b = mrac_config_z.basis;    /* vp 12: 0 or S10X (ext 6 battery, ext 0..3 mrac_z_feat_mask) */
    else if (axis == MRAC_AXIS_X) b = mrac_config_x.basis;    /* x/y: S6 only (GenExt adds nothing) */
    else if (axis == MRAC_AXIS_Y) b = mrac_config_y.basis;
    return (int)(b + 0.5f);
}
#endif

static void MRAC_GenStructured(MRAC_Axis_e axis, const MRAC_Bus_t *bus, float *phi)
{
    
    phi[0] = 1.0f;
    phi[1] = bus->x;
     
    phi[2] = bus->x * tanhf(bus->x);
#if INCLUDE_CONTROL_IN_REGRESSOR == 1
    if (MRAC_AXIS_NO_CROSS(axis)) {
        phi[3] = 0.0f;
    } else {
        phi[3] = bus->cross;
    }
    phi[4] = bus->u_nom;
    phi[5] = bus->xm;
#else
    if (MRAC_AXIS_NO_CROSS(axis)) {
        phi[3] = bus->u_nom;
    } else {
        phi[3] = bus->cross;
    }
#endif
#if MRAC_VARIANT == MRAC_VARIANT_MULTI
    /* sim RBF sets keep only u_nom and xm of S6 (features(): Gaussians + [un, pmr]) */
    if ((MRAC_BasisOf(axis) >= MRAC_BASIS_RBF6 && MRAC_BasisOf(axis) <= MRAC_BASIS_RBF24) ||
        MRAC_BasisOf(axis) == MRAC_BASIS_RBF24T || MRAC_BasisOf(axis) == MRAC_BASIS_RBF24D ||
        MRAC_BasisOf(axis) == MRAC_BASIS_S10XR16) {
        phi[0] = 0.0f;
        phi[1] = 0.0f;
        phi[2] = 0.0f;
        phi[3] = 0.0f;
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
        xa = ((axis == MRAC_AXIS_PITCH) ? imu_data.pit : imu_data.rol) * MRAC_DEG2RAD / cfg->rbf_ang_scale;
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
#elif MRAC_VARIANT == MRAC_VARIANT_MULTI
/* FW-B ext block: slots 0..23 hold the sim set picked by cfg->basis (sim_core.py features(): rate / 5 rad/s,
 * angle / 0.5 rad, accel / 50; RBF grids linspace(-1, 1), width = spacing, index i*b + j as meshgrid 'ij').
 * un = u_nom / u_max: the firmware u_nom is a torque in Nm, the sim's is PID units / 300 (PROPOSED scale).
 * Unused slots, yaw, z, basis 0 and a non-finite input give 0, so they add nothing to Phi_sq or u_ad. */
static const float mrac_rbf_rate_c[4] = {-1.5f, -0.5f, 0.5f, 1.5f};
static const float mrac_rbf_ang_c[3]  = {-1.0f,  0.0f, 1.0f};
static float mrac_acc_f[AXES];
static float mrac_x_prev[AXES];

/* a x b separable Gaussian grid on (pr, ph), centres linspace(-1, 1), width = spacing (a <= 6, b <= 4) */
static void MRAC_ExtGrid(float pr, float ph, int a, int b, float *phi)
{
    float gr[6];
    float ga[4];
    float wr = 2.0f / (float)(a - 1);
    float wa = 2.0f / (float)(b - 1);
    float d;
    int i, j;

    for (i = 0; i < a; i++) { d = (pr + 1.0f - wr * (float)i) / wr; gr[i] = expf(-0.5f * d * d); }
    for (j = 0; j < b; j++) { d = (ph + 1.0f - wa * (float)j) / wa; ga[j] = expf(-0.5f * d * d); }
    for (i = 0; i < a; i++) {
        for (j = 0; j < b; j++) phi[i * b + j] = gr[i] * ga[j];
    }
}

/* vp 13 / 14 inputs: rate and tilt as shares of their limits (200 deg/s commanded rate, 15 deg tilt) */
#define MRAC_LIM_RATE 3.4906585f
#define MRAC_LIM_TILT 0.2617994f
static const float mrac_t_rate_c[6] = {-0.7f, -0.3f, -0.1f, 0.1f, 0.3f, 0.7f};
static const float mrac_t_ang_c[4]  = {-0.6f, -0.2f, 0.2f, 0.6f};

/* Maskable feature basis (docs/design/vp-maskable-basis.md, 2026-10-09): 6 rate x 6 tilt = 36 RBF cells
 * (gap-width, normalised to MRAC_LIM_RATE / MRAC_LIM_TILT), then 4 physics features in ext slots 36-39.
 * Tilt centres dense near 0 and reaching +-1.0 (= +-15 deg, the hard limit) so the static map covers
 * the arm envelope without a separate basis. Rate centres same as RBF24T. */
static const float mrac_t40_rate_c[6] = {-0.7f, -0.3f, -0.1f, 0.1f, 0.3f, 0.7f};
static const float mrac_t40_ang_c[6]  = {-1.0f, -0.6f, -0.2f, 0.2f, 0.6f, 1.0f};

/* n Gaussians at centres c, each width = mean gap to its neighbours (an edge centre: its one gap) */
static void MRAC_Gap1D(float x, const float *c, int n, float *g)
{
    float w, d;
    int k;

    for (k = 0; k < n; k++) {
        if (k == 0) w = c[1] - c[0];
        else if (k == n - 1) w = c[n - 1] - c[n - 2];
        else w = 0.5f * (c[k + 1] - c[k - 1]);
        d = (x - c[k]) / w;
        g[k] = expf(-0.5f * d * d);
    }
}

/* RBF24T: 6 x 4 gap-width grid, phi[i * 4 + j] */
static void MRAC_ExtGap(float pr, float ph, float *phi)
{
    float gr[6];
    float ga[4];
    int i, j;

    MRAC_Gap1D(pr, mrac_t_rate_c, 6, gr);
    MRAC_Gap1D(ph, mrac_t_ang_c, 4, ga);
    for (i = 0; i < 6; i++) {
        for (j = 0; j < 4; j++) phi[i * 4 + j] = gr[i] * ga[j];
    }
}

/* RBF40T_BPQ (single maskable feature basis, docs/design/vp-maskable-basis.md): 6 x 6 = 36 RBF cells, then
 * 4 physics features in ext 36-39. The vp row sets the mask to dial the feature subset (vp 22..25). The
 * 4 physics features use the same signal sources as the S10X basis (ANF swing ip/qd, body rate derivative,
 * thrust x tilt), so the mask can mix and match with the S10X RBF cells without doubling the input layer. */
static void MRAC_ExtGap40(float pr, float ph, float sw_ip, float sw_qd, float angacc, float un_tilt, float *phi)
{
    float gr[6];
    float ga[6];
    int i, j;

    MRAC_Gap1D(pr, mrac_t40_rate_c, 6, gr);
    MRAC_Gap1D(ph, mrac_t40_ang_c,  6, ga);
    for (i = 0; i < 6; i++) {
        for (j = 0; j < 6; j++) phi[i * 6 + j] = gr[i] * ga[j];
    }
    /* 4 physics features: rate-major with 6 tilts puts slots 36-39 at the END of the 6x6 grid; we keep
     * them at the very end (i*6+j == 36..39) for clarity, by writing them in after the loop */
    phi[36] = sw_qd;        /* acc_bpq: swing quadrature (the #1 feature from feature_id_rope_phys) */
    phi[37] = sw_ip;        /* acc_bp:  swing in-phase */
    phi[38] = angacc;       /* angacc:  10 Hz-filtered body rate derivative (S10 phi[3] reused) */
    phi[39] = un_tilt;      /* thrust_x_tilt: u_nom * own tilt, the lever-arm effect */
}

/* RBF24D: level l at scale s = 2^-l: rate centres {-s, 0, s} width s, tilt {-s, s} width 2s, phi[l * 6 + i * 2 + j] */
static void MRAC_ExtDoll(float pr, float ph, float *phi)
{
    float s = 1.0f;
    float gr[3];
    float ga[2];
    float d;
    int l, i, j;

    for (l = 0; l < 4; l++) {
        for (i = 0; i < 3; i++) { d = (pr - s * (float)(i - 1)) / s; gr[i] = expf(-0.5f * d * d); }
        for (j = 0; j < 2; j++) { d = (ph - s * (float)(2 * j - 1)) / (2.0f * s); ga[j] = expf(-0.5f * d * d); }
        for (i = 0; i < 3; i++) {
            for (j = 0; j < 2; j++) phi[l * 6 + i * 2 + j] = gr[i] * ga[j];
        }
        s *= 0.5f;
    }
}

/* vp 12 state: swing tracker (pitch / roll), motor lag filter (per axis) */
static float mrac_anf_x[2], mrac_anf_v[2], mrac_anf_th[2], mrac_anf_lp[2];
static float mrac_lag_l[AXES];

/* vp 12 swing tracker: Hsu-Ortega-Damm adaptive notch on high-passed body accel (m/s^2). Writes the swing
 * in-phase / quadrature (m/s^2, unit gain once locked) and the tracked frequency (mrac_vp12.anf_hz). */
static void MRAC_AnfStep(int a, float y_raw, float *ip, float *qd)
{
    const MRAC_Vp12_t *p = &mrac_vp12;
    float th = mrac_anf_th[a];
    float x = mrac_anf_x[a];
    float v = mrac_anf_v[a];
    float y, amp;

    /* 0.1 Hz low-pass removed, so a steady tilt does not bias the quadrature output */
    mrac_anf_lp[a] += MRAC_DT * 0.62831853f * (y_raw - mrac_anf_lp[a]);
    y = y_raw - mrac_anf_lp[a];
    if (!(th > 0.0f)) th = 6.2831853f * p->anf_f0;
    v += MRAC_DT * (th * th * (y - x) - 2.0f * p->anf_zeta * th * v);
    x += MRAC_DT * v;
    amp = 2.0f * p->anf_zeta * sqrtf(x * x + v * v / (th * th));
    /* frequency frozen while the swing is too small to track */
    if (amp > p->anf_amin) {
        th -= MRAC_DT * p->anf_gamma * x * (th * th * y - 2.0f * p->anf_zeta * th * v)
              / (th * th * x * x + v * v + 1e-6f);
    }
    if (th < 6.2831853f * p->anf_fmin) th = 6.2831853f * p->anf_fmin;
    if (th > 6.2831853f * p->anf_fmax) th = 6.2831853f * p->anf_fmax;
    if (!(x - x == 0.0f) || !(v - v == 0.0f) || !(th - th == 0.0f) || !(mrac_anf_lp[a] - mrac_anf_lp[a] == 0.0f)) {
        x = 0.0f; v = 0.0f; mrac_anf_lp[a] = 0.0f;
        th = 6.2831853f * p->anf_f0;
    }
    mrac_anf_x[a] = x;
    mrac_anf_v[a] = v;
    mrac_anf_th[a] = th;
    mrac_vp12.anf_hz[a] = th / 6.2831853f;
    *ip = 2.0f * p->anf_zeta * v / th;
    *qd = -2.0f * p->anf_zeta * x;
}

/* vp 12 battery sag feature: u * (V_arm / V - 1) / sag_ref, 0 until V_arm and V are valid */
static float MRAC_SagPhi(float un)
{
    float vb = mrac_in_vbat;
    if (!(mrac_vp12.v_arm > 1.0f) || !(vb > 1.0f) || !(mrac_vp12.sag_ref > 0.0f)) return 0.0f;
    return un * (mrac_vp12.v_arm / vb - 1.0f) / mrac_vp12.sag_ref;
}

/* 24-bit ext-slot mask held in one float: NaN / negative = all on */
static uint32_t MRAC_MaskOf(float m)
{
    if (!(m >= 0.0f) || m >= 16777215.0f) return 0xFFFFFFUL;
    /* from 2^23 up every float is an integer and m + 0.5f rounds to even: 16777215 + 0.5f = 16777216 = bit 24
     * only, every ext slot off (2026-10-08 exp15: vp 12/13/14 flew with the ext block dead) */
    return (m < 8388608.0f) ? (uint32_t)(m + 0.5f) : (uint32_t)m;
}

/* Combine two 24-bit float masks (low = ext 0-23, high = ext 24-39) into a 48-bit mask; each half is
 * "all on" if the float is NaN, negative, or >= 2^24. We return a 64-bit value so a single comparison
 * covers the whole 40-cell ext block. */
static uint64_t MRAC_Mask48(const float m[2])
{
    uint32_t lo = MRAC_MaskOf(m[0]);
    uint32_t hi = MRAC_MaskOf(m[1]);
    return ((uint64_t)hi << 24) | (uint64_t)lo;
}

static void MRAC_ApplyMask(MRAC_Axis_e axis, float *phi)
{
    uint64_t m = MRAC_Mask48(mrac_vp12.mask_act[axis]);
    int k;
    for (k = 0; k < MRAC_N_EXT; k++) {
        if (!(m & (1ULL << (uint32_t)k))) phi[k] = 0.0f;
    }
}

/* vp 12, once per tick from MRAC_GateStep: latch V_arm on the arm edge; move the requested masks to the applied
 * ones unless held (ch8 on in flight, the vp rule), zeroing the weights of slots turned off. */
static void MRAC_Vp12Step(uint8_t hold, uint8_t arm_edge)
{
    MRAC_AxisState_t *st[AXES];
    uint64_t req, off;
    int a, k;

    for (a = 0; a < AXES; a++) st[a] = mrac_st_of[a];
    if (arm_edge) mrac_vp12.v_arm = mrac_in_vbat;
    if (hold) return;
    for (a = 0; a < AXES; a++) {
        req = MRAC_Mask48(mrac_vp12.mask[a]);
        off = MRAC_Mask48(mrac_vp12.mask_act[a]) & ~req;
        for (k = 0; k < MRAC_N_EXT; k++) {
            if (off & (1ULL << (uint32_t)k)) {
                st[a]->Theta[MRAC_N_STRUCT + k] = 0.0f;
                st[a]->Whatf[MRAC_N_STRUCT + k] = 0.0f;
            }
        }
        mrac_vp12.mask_act[a][0] = mrac_vp12.mask[a][0];
        mrac_vp12.mask_act[a][1] = mrac_vp12.mask[a][1];
    }
}

/* Z features (ext 0..3 of the Z axis, basis S10X only), picked by mrac_z_feat_mask:
 *   bit0 bias    1
 *   bit1 ground  clamp((h0 / max(h, h_min))^2, 0, 1)        h = mrac_in_h (Z_posPID.FB, m)
 *   bit2 drag    clamp(vz |vz| / vz_ref^2, -1, 1)            vz = Z_ratePID.FB (m/s), the Z bus x
 *   bit3 tilt    1 / max(cos(rol) cos(pit), cos(tilt_max)) - 1, in [0, 1]   (imu_data deg)
 * A NaN or out-of-range input gives 0 for that feature. Mask 0 leaves ext 0..3 at 0, as before the features.
 * The applied vp 12 mask of Z (mrac_vp12.mask_act[Z], default all on) must keep bits 0..3 on.
 *
 * MRAC_Z_FEAT_ROW fields (all PROPOSED, not measured on the rig):
 *   @h0_m          m    [0.02, 0.5]   ground-effect reference height (feature is 1 at and below it)
 *   @h_min_m       m    [0.005, 0.5]  height floor of the 1/h^2 term (keep it below h0_m)
 *   @h_max_m       m    [1, 10]       heights above this are out of range (feature 0)
 *   @vz_ref_mps    m/s  [0.2, 5]      climb rate where the drag feature reaches 1
 *   @vz_max_mps    m/s  [1, 10]       climb rates above this are out of range (feature 0)
 *   @tilt_max_deg  deg  [5, 45]       tilt clamp of the tilt-loss feature (own value, not the prearm limit) */
typedef struct { float h0_m, h_min_m, h_max_m, vz_ref_mps, vz_max_mps, tilt_max_deg; } mrac_zfeat_t;
#define MRAC_Z_FEAT_ROW(h0_m, h_min_m, h_max_m, vz_ref_mps, vz_max_mps, tilt_max_deg) \
    { (h0_m), (h_min_m), (h_max_m), (vz_ref_mps), (vz_max_mps), (tilt_max_deg) }
static const mrac_zfeat_t s_zfeat =
/*              h0_m   h_min_m h_max_m vz_ref_mps vz_max_mps tilt_max_deg */
    MRAC_Z_FEAT_ROW(0.10f, 0.02f,  4.0f,   1.0f,      5.0f,      30.0f);    /* PROPOSED */

/* MRAC_Z_SLOT_ROW fields, one row per feature in bit order (all PROPOSED):
 *   @gamma         -    [0, 10]       adaptation gain of the slot (MRAC_SetRow)
 *   @lim_share     -    [0, 0.3]      weight limit as a share of the Z u_max                              */
typedef struct { float gamma, lim_share; } mrac_zslot_t;
#define MRAC_Z_SLOT_ROW(gamma, lim_share) { (gamma), (lim_share) }
static const mrac_zslot_t s_zslot[MRAC_ZFEAT_N] = {
/*                  gamma  lim_share */
    MRAC_Z_SLOT_ROW(2.0f,  0.15f),     /* bias    PROPOSED */
    MRAC_Z_SLOT_ROW(2.0f,  0.10f),     /* ground  PROPOSED */
    MRAC_Z_SLOT_ROW(2.0f,  0.10f),     /* drag    PROPOSED */
    MRAC_Z_SLOT_ROW(2.0f,  0.10f),     /* tilt    PROPOSED */
};

static float MRAC_Clamp(float v, float lo, float hi)
{
    return (v < lo) ? lo : (v > hi) ? hi : v;
}

/* Fills phi[0..3] (ext-relative) from the mask bits; the caller zeroed phi. */
static void MRAC_ZFeat(float vz, float *phi)
{
    uint8_t m = mrac_z_feat_mask;
    float h = mrac_in_h;
    float rol = imu_data.rol;
    float pit = imu_data.pit;
    float hh, ct, ct_min;

    if (m & MRAC_ZFEAT_BIAS) phi[0] = 1.0f;
    if ((m & MRAC_ZFEAT_GROUND) && h - h == 0.0f && h >= 0.0f && h <= s_zfeat.h_max_m) {
        hh = (h > s_zfeat.h_min_m) ? h : s_zfeat.h_min_m;
        phi[1] = MRAC_Clamp((s_zfeat.h0_m / hh) * (s_zfeat.h0_m / hh), 0.0f, 1.0f);
    }
    if ((m & MRAC_ZFEAT_DRAG) && vz - vz == 0.0f && fabsf(vz) <= s_zfeat.vz_max_mps)
        phi[2] = MRAC_Clamp(vz * fabsf(vz) / (s_zfeat.vz_ref_mps * s_zfeat.vz_ref_mps), -1.0f, 1.0f);
    if ((m & MRAC_ZFEAT_TILT) && rol - rol == 0.0f && pit - pit == 0.0f
        && fabsf(rol) <= 180.0f && fabsf(pit) <= 180.0f) {
        ct = cosf(rol * MRAC_DEG2RAD) * cosf(pit * MRAC_DEG2RAD);
        ct_min = cosf(s_zfeat.tilt_max_deg * MRAC_DEG2RAD);
        if (ct < ct_min) ct = ct_min;
        phi[3] = MRAC_Clamp(1.0f / ct - 1.0f, 0.0f, 1.0f);
    }
}

static void MRAC_GenExt(MRAC_Axis_e axis, const MRAC_Bus_t *bus, float *phi)
{
    const MRAC_AxisConfig_t *cfg = (axis == MRAC_AXIS_PITCH) ? &mrac_config_pitch
                                 : (axis == MRAC_AXIS_ROLL) ? &mrac_config_roll : &mrac_config_z;  /* yaw/x/y return below */
    float ang;
    float pr;
    float un;
    float sw_ip = 0.0f, sw_qd = 0.0f;
    int basis;
    int i, j;

    for (i = 0; i < MRAC_N_EXT; i++) phi[i] = 0.0f;
    if (axis == MRAC_AXIS_Z) {
        /* vp 12: Z gets the battery slot (ext 6) and, with mrac_z_feat_mask set, the features in ext 0..3 */
        un = (cfg->u_max > 0.0f) ? bus->u_nom / cfg->u_max : 0.0f;
        if (MRAC_BasisOf(axis) == MRAC_BASIS_S10X && un - un == 0.0f) phi[6] = MRAC_SagPhi(un);
        if (MRAC_BasisOf(axis) == MRAC_BASIS_S10X && mrac_z_feat_mask != 0U) MRAC_ZFeat(bus->x, phi);
        MRAC_ApplyMask(axis, phi);
        return;
    }
    if (axis != MRAC_AXIS_PITCH && axis != MRAC_AXIS_ROLL) return;
    /* swing tracker runs every tick, so a basis switch starts from a locked tracker */
    {
        float acc = mrac_in_acc[axis];
        if (acc - acc == 0.0f) MRAC_AnfStep((int)axis, acc, &sw_ip, &sw_qd);
    }
    if (!(bus->x - bus->x == 0.0f)) return;
    /* 10 Hz filtered rate derivative, run every tick so a basis switch starts from a settled value */
    mrac_acc_f[axis] += MRAC_DT * 62.831853f * ((bus->x - mrac_x_prev[axis]) / MRAC_DT - mrac_acc_f[axis]);
    mrac_x_prev[axis] = bus->x;
    if (!(mrac_acc_f[axis] - mrac_acc_f[axis] == 0.0f)) mrac_acc_f[axis] = 0.0f;

    basis = MRAC_BasisOf(axis);
    ang = ((axis == MRAC_AXIS_PITCH) ? imu_data.pit : imu_data.rol) * MRAC_DEG2RAD;
    un = (cfg->u_max > 0.0f) ? bus->u_nom / cfg->u_max : 0.0f;
    if (!(ang - ang == 0.0f) || !(un - un == 0.0f)) return;
    /* motor lag: first-order copy of u_nom through tau, run every tick */
    if (mrac_vp12.lag_tau > MRAC_DT) mrac_lag_l[axis] += MRAC_DT / mrac_vp12.lag_tau * (un - mrac_lag_l[axis]);
    else mrac_lag_l[axis] = un;
    if (!(mrac_lag_l[axis] - mrac_lag_l[axis] == 0.0f)) mrac_lag_l[axis] = un;
    pr = bus->x / 5.0f;
    switch (basis) {
        case MRAC_BASIS_S10:
        case MRAC_BASIS_S10X:
        case MRAC_BASIS_S10XR16:
            phi[0] = sinf(ang);
            phi[1] = fabsf(pr) * un;
            phi[2] = un * fabsf(un);
            phi[3] = mrac_acc_f[axis] / 50.0f;
            if (basis != MRAC_BASIS_S10 && mrac_vp12.swing_ref > 0.0f) {
                phi[4] = sw_ip / mrac_vp12.swing_ref;
                phi[5] = sw_qd / mrac_vp12.swing_ref;
                phi[6] = MRAC_SagPhi(un);
                phi[7] = un - mrac_lag_l[axis];
            }
            if (basis == MRAC_BASIS_S10XR16) {
                float g24[24];
                MRAC_ExtGap(bus->x / MRAC_LIM_RATE, ang / MRAC_LIM_TILT, g24);
                for (i = 0; i < 16; i++) phi[8 + i] = g24[4 + i];
            }
            break;
        case MRAC_BASIS_RBF6:  MRAC_ExtGrid(pr, ang / 0.5f, 3, 2, phi); break;
        case MRAC_BASIS_RBF12: MRAC_ExtGrid(pr, ang / 0.5f, 4, 3, phi); break;
        case MRAC_BASIS_RBF24: MRAC_ExtGrid(pr, ang / 0.5f, 6, 4, phi); break;
        case MRAC_BASIS_RBF24T: MRAC_ExtGap(bus->x / MRAC_LIM_RATE, ang / MRAC_LIM_TILT, phi); break;
        case MRAC_BASIS_RBF24D: MRAC_ExtDoll(bus->x / MRAC_LIM_RATE, ang / MRAC_LIM_TILT, phi); break;
        case MRAC_BASIS_RBF40T_BPQ:
            /* Single maskable feature basis (docs/design/vp-maskable-basis.md): 36 RBF + 4 physics.
             * The vp row sets the mask; the helper writes every cell, the mask kills the unused ones. */
            MRAC_ExtGap40(bus->x / MRAC_LIM_RATE, ang / MRAC_LIM_TILT,
                          (mrac_vp12.swing_ref > 0.0f) ? sw_ip / mrac_vp12.swing_ref : 0.0f,
                          (mrac_vp12.swing_ref > 0.0f) ? sw_qd / mrac_vp12.swing_ref : 0.0f,
                          mrac_acc_f[axis] / 50.0f,
                          un * (ang / MRAC_LIM_TILT), phi);
            break;
        case MRAC_BASIS_S6RBF12: {
            float gr[4];
            float ga[3];
            float xr = bus->x / cfg->rbf_rate_scale;
            float xa = ang / cfg->rbf_ang_scale;
            for (i = 0; i < 4; i++) gr[i] = MRAC_Simple_RBF(xr, mrac_rbf_rate_c[i], 1.0f);
            for (j = 0; j < 3; j++) ga[j] = MRAC_Simple_RBF(xa, mrac_rbf_ang_c[j], 1.0f);
            for (i = 0; i < 4; i++) {
                for (j = 0; j < 3; j++) phi[i * 3 + j] = gr[i] * ga[j];
            }
            break;
        }
        default: break;
    }
    MRAC_ApplyMask(axis, phi);
}
#endif

const MRAC_BlockDesc_t mrac_block_table[] = {
    {MRAC_BLK_STRUCT, 0, MRAC_N_STRUCT, MRAC_GenStructured}
#if MRAC_VARIANT == MRAC_VARIANT_STRUCT6_RBF12
   ,{MRAC_BLK_RBF, MRAC_N_STRUCT, MRAC_N_RBF, MRAC_GenRBF}
#elif MRAC_VARIANT == MRAC_VARIANT_MULTI
   ,{MRAC_BLK_RBF, MRAC_N_STRUCT, MRAC_N_EXT, MRAC_GenExt}
#endif
};
#define MRAC_N_BLOCKS ((int)(sizeof(mrac_block_table) / sizeof(mrac_block_table[0])))

float mrac_g_gamma[AXES][MRAC_N_GROUPS] MRAC_CCM;
float mrac_g_sigma[AXES][MRAC_N_GROUPS] MRAC_CCM;
float mrac_g_phi[AXES][MRAC_N_GROUPS] MRAC_CCM;
float mrac_u_ff[AXES] MRAC_CCM;

MRAC_Cyc_t mrac_cyc;

// L2 feature gating (mrac.h). Defaults PROPOSED (spec section 2), mode 0 = off on every axis.
/* MRAC_L2G_ROW legend
   @tau_s     s  [0.05, 10]  score EMA time constant
   @thr_off   -  [0, 1]      trap below thr_off * max score (relative to the axis' best feature)
   @thr_on    -  [0, 1]      revive above thr_on * max score; > thr_off (hysteresis)
   @t_trap_s  s  [0.05, 30]  time below thr_off before the feature is trapped */
#define MRAC_L2G_ROW(tau_s, thr_off, thr_on, t_trap_s) { 0U, (tau_s), (thr_off), (thr_on), (t_trap_s) }
MRAC_L2G_Config_t mrac_l2g_cfg[AXES] = {
    MRAC_L2G_ROW(1.0f, 0.05f, 0.15f, 2.0f),   /* pitch */
    MRAC_L2G_ROW(1.0f, 0.05f, 0.15f, 2.0f),   /* roll  */
    MRAC_L2G_ROW(1.0f, 0.05f, 0.15f, 2.0f),   /* yaw   */
    MRAC_L2G_ROW(1.0f, 0.05f, 0.15f, 2.0f),   /* z     */
    MRAC_L2G_ROW(1.0f, 0.05f, 0.15f, 2.0f),   /* x     */
    MRAC_L2G_ROW(1.0f, 0.05f, 0.15f, 2.0f)    /* y     */
};
float mrac_l2g_ema[AXES][MRAC_N_FEATURES] MRAC_CCM;
uint16_t mrac_l2g_trap_ticks[AXES][MRAC_N_FEATURES] MRAC_CCM;
uint8_t mrac_l2g_trapped[AXES][MRAC_N_FEATURES] MRAC_CCM;
static float mrac_l2g_in[AXES][MRAC_N_FEATURES] MRAC_CCM;   // signed weight steps fed since the last update
static uint8_t mrac_l2g_fed[AXES] MRAC_CCM;                 // features fed (the axis' n), 0 = not fed

// Experiment telemetry block (mrac.h, filled by MRAC_XtUpdate) and its float group EMA state.
MRAC_Xt_t mrac_xt MRAC_CCM;
static float mrac_xt_grp_f[AXES][MRAC_N_GROUPS] MRAC_CCM;

// Adds this tick's signed weight step of feature i (the excitation-driven part only) to the L2 score input.
static void MRAC_L2G_Feed(MRAC_Axis_e axis, int i, int n, float step)
{
    mrac_l2g_in[axis][i] += step;
    if (mrac_l2g_fed[axis] < (uint8_t)n) mrac_l2g_fed[axis] = (uint8_t)n;
}

// 1 when L2 holds feature i of this axis (mode 1 and trapped); mode 0 and 2 never hold.
static int MRAC_L2G_Held(MRAC_Axis_e axis, int i)
{
    return mrac_l2g_cfg[axis].mode == 1U && mrac_l2g_trapped[axis][i] != 0U;
}

// Clears an axis' scores, traps and pending input (mode change, CMD 0x1D).
static void MRAC_L2G_Reset(uint8_t axis)
{
    int i;
    for (i = 0; i < MRAC_N_FEATURES; i++) {
        mrac_l2g_ema[axis][i] = 0.0f;
        mrac_l2g_trap_ticks[axis][i] = 0U;
        mrac_l2g_trapped[axis][i] = 0U;
        mrac_l2g_in[axis][i] = 0.0f;
    }
    mrac_l2g_fed[axis] = 0U;
}

// Once per tick, before the axis updates: scores last tick's steps, then traps or revives each feature.
static void MRAC_L2_Update(void)
{
    int a, i, n;
    float k, s, s_max;
    uint32_t trap_ticks;
    for (a = 0; a < AXES; a++) {
        const MRAC_L2G_Config_t *c = &mrac_l2g_cfg[a];
        n = (int)mrac_l2g_fed[a];
        if (c->mode == 0U || n == 0) continue;
        k = MRAC_DT / c->tau_s;
        s_max = 0.0f;
        for (i = 0; i < n; i++) {
            mrac_l2g_ema[a][i] += k * (mrac_l2g_in[a][i] - mrac_l2g_ema[a][i]);
            mrac_l2g_in[a][i] = 0.0f;
            s = fabsf(mrac_l2g_ema[a][i]);
            if (s > s_max) s_max = s;
        }
        mrac_l2g_fed[a] = 0U;
        if (!(s_max > 0.0f)) continue;
        trap_ticks = (uint32_t)(c->t_trap_s / MRAC_DT + 0.5f);
        for (i = 0; i < n; i++) {
            s = fabsf(mrac_l2g_ema[a][i]);
            if (s < c->thr_off * s_max) {
                if (mrac_l2g_trap_ticks[a][i] < 0xFFFFU) mrac_l2g_trap_ticks[a][i]++;
                if (mrac_l2g_trap_ticks[a][i] >= trap_ticks) mrac_l2g_trapped[a][i] = 1U;
            } else {
                mrac_l2g_trap_ticks[a][i] = 0U;
                if (s > c->thr_on * s_max) mrac_l2g_trapped[a][i] = 0U;
            }
        }
    }
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
// One control tick of one axis, MRAC_UpdateAxis, in these steps:
//   MRAC_FeatureCount   features in use (V3 RBF block on/off)
//   MRAC_RefModelStep   1. command delay (V1), reference model, closed-loop reference pull (PR)
//   MRAC_ErrorStep      2. e = x - xm, filtered e_dot, 3L leaky error integral
//   (inline)            4. bus fill and basis vector Phi, L3 feedforward; hard freeze; simplex freeze
//   MRAC_DriveSignal    5a. Lyapunov drive s (+ V1 normalized drive, 3L angle term, ST gain and barrier)
//   MRAC_AdaptWeights   5b. projected, leaked gradient step on Theta (+ Whatf low-frequency copy)
//   MRAC_AdaptiveOutput 6. u_ad = Theta'Phi (+ PR), variant guard, low-pass, u_max clamp
// ------------------------------------------------------------------------------

// *n = features in use: MRAC_N_STRUCT while V3 rbf_on is 0, else MRAC_N_FEATURES. Returns the RBF variant
// bit when the RBF block runs, else 0.
static uint16_t MRAC_FeatureCount(const MRAC_AxisConfig_t* config, int* n)
{
    *n = MRAC_N_FEATURES;
#if MRAC_VARIANT == MRAC_VARIANT_STRUCT6_RBF12
    if (config->rbf_on < 0.5f) {
        *n = MRAC_N_STRUCT;
        return 0U;
    }
    return MRAC_VID_RBF;
#elif MRAC_VARIANT == MRAC_VARIANT_MULTI
    {   /* features in use per basis: S6 6, S10 10, RBF6 12, RBF12 18, RBF24 30, S6+RBF12 18, S10X 14, RBF24T 30,
         * RBF24D 30, S10X+RBF16 30 */
        static const uint8_t n_of[MRAC_BASIS_N_X] = {6U, 10U, 12U, 18U, 30U, 18U, 14U, 30U, 30U, 30U};
        int b = (int)(config->basis + 0.5f);
        if (b <= 0 || b >= MRAC_BASIS_N_X) {
            *n = MRAC_N_STRUCT;
            return 0U;
        }
        *n = n_of[b];
        return MRAC_VID_RBF;
    }
#else
    (void)config;
    return 0U;
#endif
}

// Step 1. ref_type is the global flag on entry; returns the reference-model type this axis runs (V1 per-axis
// override). *P is the scalar Lyapunov P of types 0 and 1 (type 2 forms its matrix P in MRAC_DriveSignal).
static int MRAC_RefModelStep(MRAC_Axis_e axis_id, MRAC_AxisState_t* state, const MRAC_AxisConfig_t* config,
                             float r, int ref_type, float* P, uint16_t* vid)
{
    float r_m;          // command into the reference model (r, or r delayed by V1 ref_delay_s)
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
        if (d > 0) *vid |= MRAC_VID_DELAY;
    }
    if (config->ref_type > -0.5f) {
        ref_type = (int)(config->ref_type + 0.5f);
        *vid |= MRAC_VID_REF_TYPE;
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
            if (bw < MRAC_BW_MIN) bw = MRAC_BW_MIN;   // P = 1/(2*bw): bw 0 divides by zero
            dx = bw * (r_m - state->xm);
            state->xm    += MRAC_DT * dx;
            state->xm_dot = dx;
            *P = 1.0f / (2.0f * bw);
            break;
        }
        case 0:
        default:
            // Passthrough: instantaneous command, infinite-bandwidth reference. e = -(PID error).
            state->xm = r_m;
            state->xm_dot = 0.0f;
            *P = 1.0f;
            break;
    }
#if MRAC_ENABLE_PERF_RECOVERY == 1
    // PR, closed-loop reference model (WP-29 [E5]): pull xm toward the plant, xm' += crm_ell*(x - xm).
    // Types 1/2 only; passthrough has no model state. crm_ell*DT <= 0.25 (MRAC_VariantParamSet).
    if (config->crm_ell > 0.0f && ref_type != 0) {
        state->xm += MRAC_DT * config->crm_ell * (state->x - state->xm);
        *vid |= MRAC_VID_CRM;
    }
#endif
    return ref_type;
}

// Step 2. Tracking error and its filtered derivative; the 3L leaky error integral.
static void MRAC_ErrorStep(MRAC_AxisState_t* state, const MRAC_AxisConfig_t* config)
{
    float raw_xdot;

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
}

// Step 5a. The drive signal s of the adaptive law, from the (tanh-bounded) error; sets the variant bits of
// the drive variants and of the variants that act later in the law.
static float MRAC_DriveSignal(MRAC_AxisState_t* state, const MRAC_AxisConfig_t* config, int ref_type, float P,
                              uint16_t* vid)
{
    float P_e = 0.0f;
    float P_edot = 0.0f;
    float s;
    float PBe;

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
        if (wn < MRAC_BW_MIN) wn = MRAC_BW_MIN;       // a0 and a1 are denominators below
        if (zeta < MRAC_ZETA_MIN) zeta = MRAC_ZETA_MIN;
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
        *vid |= MRAC_VID_DRIVE_NORM;
    }
#endif
#if MRAC_ENABLE_3L == 1
    // 3L layer 1 drive (sim ctrl_mrac3l.py:133): s = rate error + lam_ang * angle error.
    if (config->lam_ang > 0.0f) {
        s += config->lam_ang * state->e_int;
        *vid |= MRAC_VID_3L;
    }
#endif
#if MRAC_ENABLE_SET_THEORETIC == 1
    // ST (Arabi, Gruenwald, Yucelen, Nguyen 2018, IJC; notebook P2 c6): restricted potential phi(mu) = mu^2/(eps - mu)
    // on mu = |e|. Gradient gain k_st = eps * dphi/d(mu^2) = eps (eps - mu/2)/(eps - mu)^2: 1 at e = 0, grows toward
    // the bound, capped at st_phi_max (also past the bound). The sim (ctrl_p2_yucelen.py:20-24) used 1/(eps^2 - s^2),
    // not normalised. st_bar adds the log-barrier drive of the sim's MRAC_ST_Barrier, minus its value at the switch-on
    // point so it starts at 0. Both scale the gradient only (projection and leakage unchanged).
    if (config->st_eps > 0.0f) {
        float mu = fabsf(state->e);
        float k_st = config->st_phi_max;
        float s_bar = 0.0f;
        if (mu < config->st_eps) {
            float g = config->st_eps - mu;
            float k = config->st_eps * (config->st_eps - 0.5f * mu) / (g * g);
            if (k < k_st) k_st = k;
        }
        if (config->st_bar > 0.0f && mu > MRAC_ST_BAR_ALPHA * config->st_eps) {
            float rem = 1.0f - mu / config->st_eps + MRAC_ST_BAR_SMOOTH;
            if (rem < MRAC_ST_BAR_SMOOTH) rem = MRAC_ST_BAR_SMOOTH;
            s_bar = config->st_bar * config->st_eps
                    * (1.0f / rem - 1.0f / (1.0f - MRAC_ST_BAR_ALPHA + MRAC_ST_BAR_SMOOTH));
            if (state->e < 0.0f) s_bar = -s_bar;
            *vid |= MRAC_VID_ST_BAR;
        }
        s = k_st * s + s_bar;
        *vid |= MRAC_VID_ST;
    }
#endif
#if MRAC_ENABLE_LF_HIGHGAIN == 1
    if (config->lf_gain > 0.0f) *vid |= MRAC_VID_LFHG;
#endif
#if MRAC_ENABLE_PERF_RECOVERY == 1
    if (config->kappa_pr > 0.0f) *vid |= MRAC_VID_KAPPA_PR;
#endif
#if MRAC_ENABLE_SATAWARE == 1
    if (config->mu_sat > 0.0f) *vid |= MRAC_VID_SATAWARE;
#endif
    return s;
}

// Step 5b. Update adaptive weights using Lyapunov gradient descent: normalized gradient (+ sigma prior),
// projection, sigma / e-modification / low-frequency / V2 saturation leakage, LFHG gain, Whatf filter.
static void MRAC_AdaptWeights(MRAC_Axis_e axis_id, MRAC_AxisState_t* state, const MRAC_AxisConfig_t* config,
                              int n, float s, float denom, int do_adaptation)
{
    static float grad[MAX_NUM_BASIS] MRAC_CCM;
    float y;
    float sigma_e;
    float sigma_eff;
    float sigma_lf_active;
    int adapted = 0;
    int i;

    if (mrac_flags.adaptation_on && do_adaptation && mrac_inj.learn_gate) {
        float theta_scale = mrac_flags.output_injection_on ? mrac_inj.inj_alpha : 1.0f;
#if MRAC_ENABLE_3L == 1
        if (config->p_max > 0.0f) theta_scale *= state->p_gain;    // 3L-v2 D self-tuning gain
#endif
        sigma_e = 0.0f;
        if (mrac_flags.e_modification_on) {
            sigma_e = config->k_e * fabsf(state->e);
        }
        sigma_eff = config->sigma + sigma_e;
        sigma_lf_active = mrac_flags.l1_filtering_on ? config->sigma_lf : 0.0f;
#if MRAC_ENABLE_LF_HIGHGAIN == 1
        if (config->lf_gain > 0.0f) sigma_lf_active = config->sigma_lf;   // LFHG: LF learning on this axis
#endif
        adapted = 1;

        for (i = 0; i < n; i++) {
            grad[i] = (-s * state->Phi[i]) / denom;
        }
        if (mrac_l2g_cfg[axis_id].mode != 0U) {   // L2 score input: this tick's gradient, without the per-feature
            for (i = 0; i < n; i++) {             // gammas (they span 15x and would trap an excited low-gain feature)
                MRAC_L2G_Feed(axis_id, i, n, MRAC_DT * grad[i] * theta_scale);
            }
        }

        /* Opt-in prior attractor folded into the gradient BEFORE projection.
         * It is the gradient of (1/2)*sigma_prior*||Theta-Theta_prior||^2, so
         * projection must bound the combined gradient — otherwise a large
         * sigma_prior pushes Theta past What_limit (prior-D-fix). */
        if ((sigma_prior != 0.0f) && ((((unsigned)mrac_prior_pull >> (unsigned)axis_id) & 1U) != 0U)) {
            for (i = 0; i < n; i++) {
                grad[i] -= sigma_prior * (state->Theta[i] - Theta_prior[axis_id][i]);
            }
        }

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
#if MRAC_ENABLE_LF_HIGHGAIN == 1
            // LFHG (Yucelen & Calise, low-frequency learning): the high gain applies only with the sigma_lf pull
            // toward Whatf on, which keeps the high-frequency part of Theta out of u_ad.
            if (config->lf_gain > 0.0f) y *= config->lf_gain;
#endif

            if (!MRAC_L2G_Held(axis_id, i)) state->Theta[i] += MRAC_DT * y * theta_scale;

            /* Discrete-time safeguard: the band-scaling projection cannot
             * bound a single large sigma_prior-driven step (the gradient
             * magnitude can overshoot the band in one tick), so hard-bounds
             * Theta to the projection set after the update (prior-D-fix).
             * No-op when sigma_prior=0, keeping the baseline update exact. */
            if ((sigma_prior != 0.0f) && ((((unsigned)mrac_prior_pull >> (unsigned)axis_id) & 1U) != 0U)) {
                if (state->Theta[i] > config->What_limit[i]) {
                    state->Theta[i] = config->What_limit[i];
                } else if (state->Theta[i] < config->What_lower_limit[i]) {
                    state->Theta[i] = config->What_lower_limit[i];
                }
            }

            // L1-style low-frequency leakage: pull fast weights toward filtered copy.
            // PR (kappa_pr > 0) needs the filtered copy too, without the sigma_lf pull.
#if MRAC_ENABLE_PERF_RECOVERY == 1 || MRAC_ENABLE_LF_HIGHGAIN == 1
            if (mrac_flags.l1_filtering_on || config->kappa_pr > 0.0f || config->lf_gain > 0.0f) {
#else
            if (mrac_flags.l1_filtering_on) {
#endif
                state->Whatf[i] += MRAC_DT * config->gam_f * (state->Theta[i] - state->Whatf[i]);
            }
        }
    }
#if MRAC_ENABLE_PERF_RECOVERY == 1
    // PR fix (WP-33): the sim filters every tick (ctrl_p2_yucelen.py:197). Before, Whatf held in the deadzone or with
    // learning off, so kappa_pr*(Theta - Whatf) stayed as a standing offset; now it decays as Theta holds.
    // The simplex freeze still holds Whatf.
    if (!adapted && config->kappa_pr > 0.0f && !(mrac_simplex.tripped || mrac_simplex.variant == 1)) {
        for (i = 0; i < n; i++) {
            state->Whatf[i] += MRAC_DT * config->gam_f * (state->Theta[i] - state->Whatf[i]);
        }
    }
#else
    (void)adapted;
#endif
}

// Step 6. Compute adaptive control component (u_ad = Theta^T * Phi), then the variant guard, the low-pass and
// the u_max clamp.
static void MRAC_AdaptiveOutput(MRAC_Axis_e axis_id, MRAC_AxisState_t* state, const MRAC_AxisConfig_t* config,
                                int n, uint16_t vid)
{
    float raw_u_ad;
    int i;

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
}

#if MRAC_ENABLE_3L == 1
#define MRAC_P_MIN 0.05f
// 3L-v2 L2 + D (see MRAC_AxisConfig_t). Runs after MRAC_AdaptWeights, before MRAC_AdaptiveOutput, so state->u_ad
// is still last tick's output: u applied = u_nom + injected u_ad (the 1-tick skew is inside pe_delay).
// m2 = |Phi|^2/(1+|Phi|^2) of this tick. No-op while gamma_c, b_axis and p_max are 0.
static void MRAC_CompositeStep(MRAC_Axis_e axis_id, MRAC_AxisState_t* state, const MRAC_AxisConfig_t* config,
                               int n, float m2, int do_adaptation)
{
    int learn = mrac_flags.adaptation_on && do_adaptation && mrac_inj.learn_gate;
    float a, u_app, nd, yhat, pf_sq, g, ph;
    int i, k;

    if (config->p_max > 0.0f) {
        if (learn) state->p_gain += MRAC_DT * (config->p_forget * state->p_gain - state->p_gain * state->p_gain * m2);
        if (!(state->p_gain >= MRAC_P_MIN)) state->p_gain = MRAC_P_MIN;   // also catches NaN
        if (state->p_gain > config->p_max) state->p_gain = config->p_max;
    } else {
        state->p_gain = 1.0f;
    }
    if (!(config->gamma_c > 0.0f) || !(config->b_axis > 0.0f)) return;

    u_app = state->u_nom + (mrac_flags.output_injection_on ? mrac_simplex.fade * mrac_inj.inj_alpha : 0.0f) * state->u_ad;
    k = (int)config->pe_delay;
    if (k < 0) k = 0;
    if (k > MRAC_REF_BUF - 1) k = MRAC_REF_BUF - 1;
    state->u_buf[state->u_idx] = u_app;
    a = MRAC_DT * config->wc_pe;
    if (!(a > 0.0f)) a = 0.0f;
    if (a > 1.0f) a = 1.0f;
    state->u_f += a * (state->u_buf[(state->u_idx + MRAC_REF_BUF - k) % MRAC_REF_BUF] - state->u_f);
    state->u_idx = (uint8_t)((state->u_idx + 1U) % MRAC_REF_BUF);
    state->xd_f += a * (state->xdot_f - state->xd_f);
    nd = state->u_f - state->xd_f / config->b_axis;       // -Delta, filtered: what u_ad = +Theta'Phi must equal

    yhat = 0.0f;
    pf_sq = 0.0f;
    for (i = 0; i < n; i++) {
        ph = state->Phi[i] * mrac_g_phi[axis_id][mrac_feature_desc[i].group];
        state->Phi_f[i] += a * (ph - state->Phi_f[i]);
        yhat += state->Theta[i] * state->Phi_f[i];
        pf_sq += state->Phi_f[i] * state->Phi_f[i];
    }
    state->pe = nd - yhat;
    if (!(state->pe - state->pe == 0.0f)) {                  // non-finite: restart the filters, skip the step
        MRAC_L2Snap(state);
        return;
    }
    if (!learn || mrac_simplex.tripped || mrac_simplex.variant == 1 || state->nan_hold != 0U) return;
    g = MRAC_DT * config->gamma_c * state->p_gain * state->pe / (1.0f + pf_sq);
    for (i = 0; i < n; i++) {
        if (mrac_l2g_cfg[axis_id].mode != 0U) MRAC_L2G_Feed(axis_id, i, n, g * state->Phi_f[i]);
        if (MRAC_L2G_Held(axis_id, i)) continue;
        state->Theta[i] += g * state->Phi_f[i];
        if (state->Theta[i] > config->What_limit[i]) {
            state->Theta[i] = config->What_limit[i];
        } else if (state->Theta[i] < config->What_lower_limit[i]) {
            state->Theta[i] = config->What_lower_limit[i];
        }
    }
}
#endif

// ------------------------------------------------------------------------------
// Runs the core MRAC algorithm for a single axis (steps listed above).
static void MRAC_UpdateAxis(MRAC_Axis_e axis_id, MRAC_AxisState_t* state, const MRAC_AxisConfig_t* config, float cross_coupling, float r)
{
    float P = 1.0f;
    float s;
    float Phi_sq;
    float denom;
    int do_adaptation;
    int i;
    int n;              // features in use: MRAC_N_STRUCT while V3 rbf_on is 0, else MRAC_N_FEATURES
    int ref_type;       // reference-model type this axis runs (global flag unless V1 ref_type >= 0)
    uint16_t vid;
    uint32_t t0, t1, t2, t3;

    t0 = MRAC_CYC_NOW();
    ref_type = mrac_flags.ref_model_type;
    vid = MRAC_FeatureCount(config, &n);
    ref_type = MRAC_RefModelStep(axis_id, state, config, r, ref_type, &P, &vid);

    MRAC_ErrorStep(state, config);

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
     * variant; u_ad is still computed and faded at the injection point.
     * Input-guard hold-off (MRAC_GuardedUpdate): Theta frozen too, the caller zeroes u_ad. */
    if (mrac_simplex.tripped || mrac_simplex.variant == 1 || state->nan_hold != 0U) {
        do_adaptation = 0;
    }

    s = MRAC_DriveSignal(state, config, ref_type, P, &vid);
#if MRAC_ENABLE_3L == 1
    if (config->gamma_c > 0.0f || config->p_max > 0.0f) vid |= MRAC_VID_3LV2;
#endif
    mrac_var_id[axis_id] = vid;

    MRAC_AdaptWeights(axis_id, state, config, n, s, denom, do_adaptation);
#if MRAC_ENABLE_3L == 1
    MRAC_CompositeStep(axis_id, state, config, n, Phi_sq / denom, do_adaptation);
#endif

    MRAC_AdaptiveOutput(axis_id, state, config, n, vid);

    MRAC_CycEnd(axis_id, t0, t1, t2, t3);
}

/* Input guard (WP-38). A non-finite x, r, u_nom or cross skips the axis for this tick: Theta, Whatf and the
 * reference model stay as they were, u_ad is 0 (PID only), and the hold-off restarts at nan_rearm. The next
 * nan_rearm finite ticks run the law with learning frozen and u_ad zeroed, so the model and filters resync;
 * then u_ad reaches the mixer again, from 0 through the omega_u filter. Before WP-38 one NaN poisoned Theta
 * and Controller_Update fell back to PID for the rest of the flight. */
static void MRAC_GuardedUpdate(MRAC_Axis_e axis_id, MRAC_AxisState_t* state, const MRAC_AxisConfig_t* config,
                               float cross_coupling, float r)
{
    float hold;

    if (!MRAC_FINITE(state->x) || !MRAC_FINITE(state->u_nom) || !MRAC_FINITE(cross_coupling) || !MRAC_FINITE(r)) {
        hold = config->nan_rearm;
        state->nan_hold = (hold >= 65535.0f) ? 65535U : ((hold > 0.0f) ? (uint16_t)hold : 0U);
        state->u_ad = 0.0f;
        return;
    }
    MRAC_UpdateAxis(axis_id, state, config, cross_coupling, r);
    if (state->nan_hold != 0U) {
        state->nan_hold--;
        state->u_ad = 0.0f;
    }
}

void MRAC_SimplexStep(void)
{
    static uint8_t prev_trigger = 0;
    const MRAC_AxisConfig_t *configs[AXES];
    MRAC_AxisState_t *axes[AXES];
    float   sum_w2;
    float   target;
    uint8_t trigger;
    uint8_t r;
    uint8_t i;
    uint8_t a;

    for (a = 0; a < AXES; a++) { configs[a] = mrac_cfg_of[a]; axes[a] = mrac_st_of[a]; }

    r = 0;
    for (a = 0; a < AXES; a++) {
        if (fabsf(axes[a]->u_ad) >= configs[a]->u_max * MRAC_SAT_FRAC) {
            if (mrac_simplex.sat_ticks[a] < 0xFFFFU) mrac_simplex.sat_ticks[a]++;
        } else {
            mrac_simplex.sat_ticks[a] = 0;
        }
        if (r == 0 && mrac_simplex.sat_ticks[a] > mrac_simplex.sat_ticks_max) r = 4;
        sum_w2 = 0.0f;
        for (i = 0; i < MRAC_N_FEATURES; i++) sum_w2 += axes[a]->Theta[i] * axes[a]->Theta[i];
        if (sqrtf(sum_w2) > mrac_simplex.w_norm_max) r = 3;
    }
    /* imu_data is in degrees (API/imu_update.c), the envelope in rad. Before WP-38 the degrees were compared as rad. */
    if (fabsf(imu_data.pit * MRAC_DEG2RAD) > mrac_simplex.pitch_max) r = 2;
    if (fabsf(imu_data.rol * MRAC_DEG2RAD) > mrac_simplex.roll_max)   r = 1;
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
        mrac_simplex.fade -= MRAC_DT / MRAC_FADE_S;
        if (mrac_simplex.fade < target) mrac_simplex.fade = target;
    } else if (mrac_simplex.fade < target) {
        mrac_simplex.fade += MRAC_DT / MRAC_FADE_S;
        if (mrac_simplex.fade > target) mrac_simplex.fade = target;
    }
}

// ------------------------------------------------------------------------------
// Public API Operations
// ------------------------------------------------------------------------------

/* MRAC_SET legend: one @line per field (row); all four axis values of the row must lie in [min, max]
   (tools/row_meta.py). Units are per axis: pitch/roll/yaw in rad, rad/s, N m; z in m/s, N. Bounds are PROPOSED
   plausibility ranges; where CMD 0x1D writes the field they are its MRAC_VAR_FIELD range, widened to 0 (off).
   @sigma_lf        1/s        [0, 5]       low-frequency leakage of Theta toward Whatf (L1-style)
   @sigma           1/s        [0, 1]       sigma-modification leakage
   @gam_f           rad/s      [0, 100]     Whatf low-pass bandwidth
   @omega_u         rad/s      [0, 100]     u_ad low-pass cutoff (ENABLE_PERFORMANCE_RECOVERY)
   @lambda_perf     rad/s      [0, 100]     unused (mrac.h)
   @tau_v           s          [0, 10]      unused (mrac.h)
   @u_max           Nm|N       [0, 50]      clamp on |u_ad|
   @mrac_to_mixer   mixer/Nm|N [10, 5000]   mixer units per N m (per N on z); DEFAULT_MRAC_TO_MIXER_* both payloads
   @J               kgm2|kg    [0, 5]       inertia (z: mass)
   @e_deadzone      rad/s|m/s  [0, 1]       no adaptation while |e| is below
   @e_freeze        rad/s|m/s  [0, 10]      hard freeze (u_ad 0, Theta held) while |e| is above
   @e_sat           rad/s|m/s  [0, 5]       tanh scale of the PBe drive
   @k_e             -          [0, 1]       e-modification gain
   @ref_model_bw    rad/s      [0.5, 100]   reference model bandwidth (wn on type 2)
   @ref_model_zeta  -          [0.1, 2]     reference model damping (type 2)
   @P_lyap          -          [0, 100]     unused legacy (ADR-0007)
   @ref_Q1          -          [0, 100]     Lyapunov Q diagonal, rate error (type 2)
   @ref_Q2          -          [0, 100]     Lyapunov Q diagonal, rate-error derivative (type 2)
   @wc_edot         rad/s      [0, 200]     low-pass cutoff of the finite-difference e_dot
   @ref_type        -          [-1, 2]      V1: -1 = global ref_model_type, else 0/1/2 for this axis
   @ref_delay_s     s          [0, 0.035]   V1: delay on r before the reference model
   @drive_norm      -          [0, 1]       V1: normalized drive on/off
   @lam_edot        s          [0, 0.1]     V1: e_dot weight of the normalized type-2 drive
   @kappa_pr        -          [0, 2]       PR: performance-recovery gain
   @crm_ell         1/s        [0, 50]      PR: closed-loop reference model gain
   @mu_sat          1/(Nms)    [0, 10]      V2: saturation-aware leakage
   @lam_ang         1/s        [0, 20]      3L: angle-error drive weight
   @rbf_on          -          [0, 1]       V3: RBF grid on/off
   @rbf_rate_scale  rad/s      [0.1, 20]    V3: rate normalisation of the grid
   @rbf_ang_scale   rad        [0.05, 1]    V3: angle normalisation of the grid
   @st_eps          rad/s      [0, 2]       ST: |e| bound of the restricted potential, 0 = off
   @st_phi_max      -          [1, 50]      ST: cap of the gradient gain
   @st_bar          -          [0, 1]       ST: log-barrier drive gain, 0 = off
   @lf_gain         -          [0, 10]      LFHG: 0 = off, else LF learning and gamma x lf_gain
   @basis           -          [0, 10]      FW-B ext feature set MRAC_BASIS_* (mrac.h), 0 = S6 only
   @nan_rearm       ticks      [0, 2000]    finite ticks after a non-finite input before u_ad reaches the mixer (200 = 1 s) */
#define MRAC_SET(f, p, r, y, z) \
    mrac_config_pitch.f = (p); \
    mrac_config_roll.f = (r); \
    mrac_config_yaw.f = (y); \
    mrac_config_z.f = (z)

/* MRAC_BASIS legend: one row per (axis, basis i); Theta_i * Phi_i is a part of u_ad (N m, z: N). PROPOSED bounds.
   @g    1/s    [0, 10]   learning rate gamma[i] (diagonal Gamma), 0 = this basis does not learn
   @lim  u/phi  [0, 2]    What_limit[i]: projection bound on Theta_i
   @t    u/phi  [0, 1]    What_tol[i]: projection boundary layer below lim
   @low  u/phi  [-2, 0]   What_lower_limit[i]: lower bound on Theta_i (0 keeps u_nom's weight from going negative) */
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
    MRAC_SET(basis,           0.0f,                      0.0f,                      0.0f,                      0.0f);
    MRAC_SET(st_eps,          0.0f,                      0.0f,                      0.0f,                      0.0f);
    MRAC_SET(st_phi_max,      10.0f,                     10.0f,                     10.0f,                     10.0f);
    MRAC_SET(st_bar,          0.0f,                      0.0f,                      0.0f,                      0.0f);
    MRAC_SET(lf_gain,         0.0f,                      0.0f,                      0.0f,                      0.0f);
    MRAC_SET(nan_rearm,       200.0f,                    200.0f,                    200.0f,                    200.0f);

    /* Basis weights: gamma = learning rate, limit/lower = weight bounds (projection),
     * tol = projection boundary layer. Yaw limit/tol = pitch/roll value * 0.6f. */
    /*         axis   i  gamma  limit        tol          lower           feature */
    MRAC_BASIS(pitch, 0, 1.50f, 0.15f,       0.03f,       -0.15f);     /* bias */
    MRAC_BASIS(pitch, 1, 0.20f, 0.05f,       0.01f,       -0.05f);     /* rate: both signs, damping (2026-10-08) */
    MRAC_BASIS(pitch, 2, 0.05f, 0.02f,       0.005f,      0.0f);       /* rate_tanh */
    MRAC_BASIS(pitch, 3, 0.05f, 0.05f,       0.01f,       0.0f);       /* cross */
    MRAC_BASIS(pitch, 4, 0.10f, 0.20f,       0.04f,       0.0f);       /* u_nom */
    MRAC_BASIS(pitch, 5, 0.10f, 0.15f,       0.03f,       0.0f);       /* xm */
    MRAC_BASIS(roll,  0, 1.50f, 0.15f,       0.03f,       -0.15f);     /* bias */
    MRAC_BASIS(roll,  1, 0.20f, 0.05f,       0.01f,       -0.05f);     /* rate: both signs, damping (2026-10-08) */
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
#elif MRAC_VARIANT == MRAC_VARIANT_MULTI
    for (i = MRAC_N_STRUCT; i < MRAC_N_FEATURES; i++) {   /* FW-B ext slots: V3 RBF row (PROPOSED); yaw / z off */
        MRAC_BASIS(pitch, i, 0.10f, 0.05f, 0.01f, -0.05f);
        MRAC_BASIS(roll,  i, 0.10f, 0.05f, 0.01f, -0.05f);
        MRAC_BASIS(yaw,   i, 0.0f, 0.0f, 0.0f, 0.0f);
        MRAC_BASIS(z,     i, 0.0f, 0.0f, 0.0f, 0.0f);
    }
#endif

    /* History and provenance (newest first)
     * 2026-10-04 WP-33  ST and LFHG rows, OFF (st_eps 0, lf_gain 0). st_phi_max 10 = the sim default
     *                   (ctrl_p2_yucelen.py:46); it matters only when st_eps > 0. Flight values: CMD 0x1D,
     *                   docs/workflow-b/mrac-variants.md (SIL limit tests, PROPOSED).
     * 2026-10-04 WP-27 Variant rows (ref_type .. rbf_ang_scale), all OFF; ref_type -1 = follow the global
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

    /* x / y velocity layers (6-axis, 2026-10-10, operator: "i want all 6 axes"). Start from the z column (same
     * translational structure, no cross term), then override. x = velocity m/s, u = world accel m/s^2;
     * mrac_to_mixer 100 = cm/s^2 per m/s^2 (locxsPID.U feeds accel_to_lean_angles in cm/s^2). Unflown: every
     * value is PROPOSED unless marked; log replay in docs/analysis/mrac-xy-replay-2026-10-10.md. axis_enable_x/y = 1
     * at boot; g_ctrl_axis_mask bits 4/5 come from kp row 7 (0x3F). */
    mrac_config_x = mrac_config_z;
    mrac_config_x.u_max         = 1.0f;     /* PROPOSED: about 5.8 deg of extra lean (computed atan(1/9.81)) */
    mrac_config_x.mrac_to_mixer = 100.0f;   /* exact: cm per m */
    mrac_config_x.J             = 1.0f;     /* unit mass: u is an acceleration */
    mrac_config_x.omega_u       = 4.0f;     /* PROPOSED: same as pitch */
    mrac_config_x.What_lower_limit[0] = -mrac_config_x.What_limit[0];   /* bias: wind / payload sign unknown */
    mrac_config_y = mrac_config_x;
    mrac_xy_ref_kind = 0xFFU;    /* forces the apply: reference model type / bw / zeta / delay from the PID row */
    mrac_xy_ref_written[MRAC_XY_REF_W_X] = 0U;   /* no explicit write yet, so the row fills every reference field */
    mrac_xy_ref_written[MRAC_XY_REF_W_Y] = 0U;
    MRAC_XyRefSelect(0U);
    mrac_xy_layers = MRAC_XY_LAYER_INNER;    /* D4: outer layer off at boot */
    MRAC_XyLayersLatch();
    mrac_att_layers = MRAC_LAYER_INNER;      /* item 1: the new groups boot as today (inner only) */
    mrac_yaw_layers = MRAC_LAYER_INNER;
    mrac_z_layers = MRAC_LAYER_INNER;
    MRAC_GroupLayersLatch();

    for (i = 0; i < (int)AXES; i++) {
        mrac_u_ff[i] = 0.0f;
        for (j = 0; j < MRAC_N_GROUPS; j++) {
            mrac_xt_grp_f[i][j] = 0.0f;
            mrac_g_gamma[i][j] = 1.0f;
            mrac_g_sigma[i][j] = 1.0f;
            mrac_g_phi[i][j] = 1.0f;
        }
    }
    for (i = 0; i < MRAC_XT_WORDS; i++) ((int16_t *)&mrac_xt)[i] = 0;

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
    mrac_flags.axis_enable_x      = 1;   /* x/y adaptation on at boot (operator "go", 2026-10-10; replay + host tests done) */
    mrac_flags.axis_enable_y      = 1;
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
        mrac_state.x_vel.Theta[i] = 0.0f;
        mrac_state.y_vel.Theta[i] = 0.0f;
        mrac_state.x_vel.Whatf[i] = 0.0f;
        mrac_state.y_vel.Whatf[i] = 0.0f;
    }
    MRAC_PriorWarmStart();

    // Snap references to plant state (bumpless) and zero 2nd-order velocity states
    mrac_state.pitch.xm = mrac_state.pitch.x;   mrac_state.pitch.xm_dot  = 0.0f;
    mrac_state.roll.xm  = mrac_state.roll.x;    mrac_state.roll.xm_dot   = 0.0f;
    mrac_state.yaw.xm   = mrac_state.yaw.x;     mrac_state.yaw.xm_dot    = 0.0f;
    mrac_state.z_rate.xm= mrac_state.z_rate.x;  mrac_state.z_rate.xm_dot = 0.0f;
    mrac_state.x_vel.xm = mrac_state.x_vel.x;   mrac_state.x_vel.xm_dot  = 0.0f;
    mrac_state.y_vel.xm = mrac_state.y_vel.x;   mrac_state.y_vel.xm_dot  = 0.0f;

    // Zero the rate-derivative estimator (ADR-0007) so e_dot starts clean post-reset.
    mrac_state.pitch.x_prev = mrac_state.pitch.x;   mrac_state.pitch.xdot_f  = 0.0f; mrac_state.pitch.e_dot  = 0.0f;
    mrac_state.roll.x_prev  = mrac_state.roll.x;    mrac_state.roll.xdot_f   = 0.0f; mrac_state.roll.e_dot   = 0.0f;
    mrac_state.yaw.x_prev   = mrac_state.yaw.x;     mrac_state.yaw.xdot_f    = 0.0f; mrac_state.yaw.e_dot    = 0.0f;
    mrac_state.z_rate.x_prev= mrac_state.z_rate.x;  mrac_state.z_rate.xdot_f = 0.0f; mrac_state.z_rate.e_dot = 0.0f;
    mrac_state.x_vel.x_prev = mrac_state.x_vel.x;   mrac_state.x_vel.xdot_f  = 0.0f; mrac_state.x_vel.e_dot  = 0.0f;
    mrac_state.y_vel.x_prev = mrac_state.y_vel.x;   mrac_state.y_vel.xdot_f  = 0.0f; mrac_state.y_vel.e_dot  = 0.0f;

    MRAC_VariantSnap(&mrac_state.pitch);
    MRAC_VariantSnap(&mrac_state.roll);
    MRAC_VariantSnap(&mrac_state.yaw);
    MRAC_VariantSnap(&mrac_state.z_rate);
    MRAC_VariantSnap(&mrac_state.x_vel);
    MRAC_VariantSnap(&mrac_state.y_vel);
}

// V1 delay ring holds the plant state (bumpless) and the 3L integral restarts at 0.
static void MRAC_VariantSnap(MRAC_AxisState_t *st)
{
    int k;
    for (k = 0; k < MRAC_REF_BUF; k++) st->r_buf[k] = st->x;
    st->e_int = 0.0f;
    MRAC_L2Snap(st);
    st->p_gain = 1.0f;
}

// 3L-v2 L2 filters restart bumpless: u ring and u_f at u_nom, xdot and Phi filters at 0.
static void MRAC_L2Snap(MRAC_AxisState_t *st)
{
    int k;
    for (k = 0; k < MRAC_REF_BUF; k++) st->u_buf[k] = st->u_nom;
    for (k = 0; k < MAX_NUM_BASIS; k++) st->Phi_f[k] = 0.0f;
    st->u_f = st->u_nom;
    st->xd_f = 0.0f;
    st->pe = 0.0f;
}

/* CMD 0x1D field table: one row per MRAC_VariantField_e, in enum order. Writes outside [lo, hi] or
 * non-finite are refused. snap = 1: the write changes the reference model, so xm snaps to the plant.
 * Bounds keep an enabled variant inside the existing u_max clamp and numerically stable:
 *   ref_delay_s  <= (MRAC_REF_BUF-1)*DT;  crm_ell*DT <= 0.25;  rbf scales > 0 (they divide).
 *   st_phi_max >= 1 (k_st is 1 at e = 0);  gam_f*DT <= 0.5;  DT*gamma(2)*gamma_scale(2)*lf_gain*sigma_lf <= 1
 *   keeps the sigma_lf pull a stable Euler step. */
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
    MRAC_VAR_FIELD( 0.5f,  100.0f, 1),   /* ref_model_bw    rad/s, wn on type 2 (DT*wn < 2) */
    MRAC_VAR_FIELD( 0.0f,  2.0f,   0),   /* st_eps          rad/s */
    MRAC_VAR_FIELD( 1.0f,  50.0f,  0),   /* st_phi_max      - */
    MRAC_VAR_FIELD( 0.0f,  1.0f,   0),   /* st_bar          - */
    MRAC_VAR_FIELD( 0.0f,  10.0f,  0),   /* lf_gain         - */
    MRAC_VAR_FIELD( 0.0f,  5.0f,   0),   /* sigma_lf        1/s */
    MRAC_VAR_FIELD( 0.5f,  100.0f, 0),   /* gam_f           rad/s */
    MRAC_VAR_FIELD( 0.0f,  MRAC_BASIS_HI_X, 0), /* basis    MRAC_BASIS_* id (6 = vp 12 S10X, 7/8 = vp 13/14, 9 = vp 15) */
    MRAC_VAR_FIELD( 0.0f,  20.0f,  0),   /* gamma_c         1/s (3L-v2 L2) */
    MRAC_VAR_FIELD( 0.0f,  2000.0f, 0),  /* b_axis          rad/s^2 per u (fit_b) */
    MRAC_VAR_FIELD( 0.0f,  7.0f,   0),   /* pe_delay        ticks, < MRAC_REF_BUF */
    MRAC_VAR_FIELD( 1.0f,  100.0f, 0),   /* wc_pe           rad/s */
    MRAC_VAR_FIELD( 0.0f,  20.0f,  0),   /* p_max           - (3L-v2 D) */
    MRAC_VAR_FIELD( 0.0f,  5.0f,   0),   /* p_forget        1/s */
    MRAC_VAR_FIELD( 0.0f,  2.0f,   0),   /* l2_mode         0 off, 1 gate, 2 observe */
    MRAC_VAR_FIELD( 0.05f, 10.0f,  0),   /* l2_tau          s */
    MRAC_VAR_FIELD( 0.0f,  1.0f,   0),   /* l2_thr_off      -, < l2_thr_on */
    MRAC_VAR_FIELD( 0.0f,  1.0f,   0),   /* l2_thr_on       -, > l2_thr_off */
    MRAC_VAR_FIELD( 0.05f, 30.0f,  0),   /* l2_t_trap       s */
    MRAC_VAR_FIELD( 0.0f,  3.0f,   0),   /* xy_layers       bits, MRAC_XY_LAYER_* (any axis) */
    MRAC_VAR_FIELD( 0.3f,  1.5f,   1),   /* ref_zeta        -, the 2nd-order reference damping (type 2 only) */
    MRAC_VAR_FIELD( 0.0f,  3.0f,   0),   /* att_layers      bits, MRAC_LAYER_* (any axis; ground latch) */
    MRAC_VAR_FIELD( 0.0f,  3.0f,   0),   /* yaw_layers      bits, MRAC_LAYER_* (any axis; ground latch) */
    MRAC_VAR_FIELD( 0.0f,  3.0f,   0)    /* z_layers        bits, MRAC_LAYER_* (any axis; ground latch) */
};

#if MRAC_VARIANT == MRAC_VARIANT_MULTI
static void MRAC_SetRow(MRAC_AxisConfig_t *c, int i, float g, float lim)
{
    c->gamma[i] = g;
    c->What_limit[i] = lim;
    c->What_tol[i] = 0.2f * lim;
    c->What_lower_limit[i] = -lim;
}

/* vp 12 rows (PROPOSED, lim = share of u_max, docs/workflow-b/vp12-design.md): set on a switch to S10X, the
 * MRAC_Init defaults restored on a switch away (the rows are shared by every basis). */
static void MRAC_Vp12Lims(uint8_t axis, MRAC_AxisConfig_t *c)
{
    int b = (int)(c->basis + 0.5f);
    int on = (b == MRAC_BASIS_S10X);
    float u = c->u_max;
    int k;

    if (axis == (uint8_t)MRAC_AXIS_PITCH || axis == (uint8_t)MRAC_AXIS_ROLL) {
        if (on) {
            MRAC_SetRow(c, 0, 1.5f, 0.10f * u);                    /* bias 10 % */
            MRAC_SetRow(c, MRAC_N_STRUCT + 4, 1.5f, 0.02f * u);    /* swing in-phase 2 % */
            MRAC_SetRow(c, MRAC_N_STRUCT + 5, 1.5f, 0.02f * u);    /* swing quadrature 2 % */
            MRAC_SetRow(c, MRAC_N_STRUCT + 6, 1.5f, 0.05f * u);    /* battery sag 5 % */
            MRAC_SetRow(c, MRAC_N_STRUCT + 7, 1.5f, 0.10f * u);    /* motor lag 10 % */
        } else if (b == MRAC_BASIS_S10XR16) {
            /* vp 15 (row ext_g 4, so config = effective): S10X rows 4-7 at 1/4 of vp 12's effective 0.375 (vp 12
             * diverged in flight 2026-10-08), rows 0-3 at vp 12's effective 0.025, same lims; the Gaussians keep
             * the MRAC_Init 0.10 / 0.05 (vp 13); bias row unused (phi[0] = 0, as RBF24T) */
            MRAC_SetRow(c, 0, 1.50f, 0.15f);
            for (k = 0; k < 4; k++) MRAC_SetRow(c, MRAC_N_STRUCT + k, 0.025f, 0.05f);
            MRAC_SetRow(c, MRAC_N_STRUCT + 4, 0.094f, 0.02f * u);
            MRAC_SetRow(c, MRAC_N_STRUCT + 5, 0.094f, 0.02f * u);
            MRAC_SetRow(c, MRAC_N_STRUCT + 6, 0.094f, 0.05f * u);
            MRAC_SetRow(c, MRAC_N_STRUCT + 7, 0.094f, 0.10f * u);
        } else if (b == MRAC_BASIS_RBF40T_BPQ) {
            /* Single maskable feature basis (docs/design/vp-maskable-basis.md, 2026-10-09): 36 RBF cells +
             * 4 physics features in ext 36-39. The vp row sets the mask; this sets the per-cell defaults:
             * RBF cells (ext 0-35) at vp 13's lim 0.05, the 4 physics cells (ext 36-39) at a wider lim
             * 0.10 since they carry the swing-band signal that the RBF cannot see. The mask kills the
             * unused cells, so the same defaults work for vp 22-25. */
            MRAC_SetRow(c, 0, 1.50f, 0.15f);                                  /* bias (unused on RBF bases) */
            for (k = 0; k < 8; k++) MRAC_SetRow(c, MRAC_N_STRUCT + k, 0.10f, 0.05f);  /* RBF cells ext 0-7 */
            for (k = 8; k < 36; k++) MRAC_SetRow(c, MRAC_N_STRUCT + k, 0.10f, 0.05f); /* RBF cells ext 8-35 */
            MRAC_SetRow(c, MRAC_N_STRUCT + 36, 0.20f, 0.10f * u);              /* acc_bpq    (slot 36) -- the #1 feature */
            MRAC_SetRow(c, MRAC_N_STRUCT + 37, 0.20f, 0.10f * u);              /* acc_bp     (slot 37) */
            MRAC_SetRow(c, MRAC_N_STRUCT + 38, 0.20f, 0.05f * u);              /* angacc     (slot 38) */
            MRAC_SetRow(c, MRAC_N_STRUCT + 39, 0.20f, 0.05f * u);              /* thrust_x_tilt (slot 39) */
        } else {
            MRAC_SetRow(c, 0, 1.50f, 0.15f);
            for (k = 0; k < 8; k++) MRAC_SetRow(c, MRAC_N_STRUCT + k, 0.10f, 0.05f);
        }
    } else if (axis == (uint8_t)MRAC_AXIS_Z) {
        if (on) MRAC_SetRow(c, MRAC_N_STRUCT + 6, 2.0f, 0.15f * u); /* battery sag 15 % */
        else MRAC_SetRow(c, MRAC_N_STRUCT + 6, 0.0f, 0.0f);
        /* Z features ext 0..3 (MRAC_Z_SLOT_ROW); their phi stays 0 while mrac_z_feat_mask is 0 */
        for (k = 0; k < MRAC_ZFEAT_N; k++)
            MRAC_SetRow(c, MRAC_N_STRUCT + k, on ? s_zslot[k].gamma : 0.0f, on ? s_zslot[k].lim_share * u : 0.0f);
    }
}
#endif

uint8_t MRAC_VariantParamSet(uint8_t axis, uint8_t field, float val)
{
    MRAC_AxisConfig_t *cfg[AXES];
    MRAC_AxisState_t *st[AXES];
    MRAC_AxisConfig_t *c;
    int k;

    for (k = 0; k < AXES; k++) { cfg[k] = mrac_cfg_of[k]; st[k] = mrac_st_of[k]; }

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
        case MRAC_VF_ST_EPS:         c->st_eps = val;         break;
        case MRAC_VF_ST_PHI_MAX:     c->st_phi_max = val;     break;
        case MRAC_VF_ST_BAR:         c->st_bar = val;         break;
        case MRAC_VF_LF_GAIN:
            c->lf_gain = val;
            // bumpless: the filtered copy starts at the weights, so the sigma_lf pull starts at 0
            for (k = 0; k < MRAC_N_FEATURES; k++) st[axis]->Whatf[k] = st[axis]->Theta[k];
            break;
        case MRAC_VF_SIGMA_LF:       c->sigma_lf = val;       break;
        case MRAC_VF_GAM_F:          c->gam_f = val;          break;
        case MRAC_VF_BASIS:
            c->basis = (float)(int)(val + 0.5f);
            // fresh ext weights on every switch, as for rbf_on
            for (k = MRAC_N_STRUCT; k < MRAC_N_FEATURES; k++) {
                st[axis]->Theta[k] = 0.0f;
                st[axis]->Whatf[k] = 0.0f;
            }
#if MRAC_VARIANT == MRAC_VARIANT_MULTI
            MRAC_Vp12Lims(axis, c);
#endif
            break;
#if MRAC_ENABLE_3L == 1
        case MRAC_VF_GAMMA_C:  c->gamma_c = val;  MRAC_L2Snap(st[axis]); break;
        case MRAC_VF_B_AXIS:   c->b_axis = val;   MRAC_L2Snap(st[axis]); break;
        case MRAC_VF_PE_DELAY: c->pe_delay = (float)(int)(val + 0.5f); MRAC_L2Snap(st[axis]); break;
        case MRAC_VF_WC_PE:    c->wc_pe = val;    MRAC_L2Snap(st[axis]); break;
        case MRAC_VF_P_MAX:    c->p_max = val;    st[axis]->p_gain = 1.0f; break;
        case MRAC_VF_P_FORGET: c->p_forget = val; break;
#endif
        // L2 gating: a mode write starts the scores fresh; the thresholds keep thr_off < thr_on (hysteresis)
        case MRAC_VF_L2_MODE:
            mrac_l2g_cfg[axis].mode = (uint8_t)(val + 0.5f);
            MRAC_L2G_Reset(axis);
            break;
        case MRAC_VF_L2_TAU:     mrac_l2g_cfg[axis].tau_s = val; break;
        case MRAC_VF_L2_THR_OFF:
            if (!(val < mrac_l2g_cfg[axis].thr_on)) return 0U;
            mrac_l2g_cfg[axis].thr_off = val;
            break;
        case MRAC_VF_L2_THR_ON:
            if (!(val > mrac_l2g_cfg[axis].thr_off)) return 0U;
            mrac_l2g_cfg[axis].thr_on = val;
            break;
        case MRAC_VF_L2_T_TRAP:  mrac_l2g_cfg[axis].t_trap_s = val; break;
        case MRAC_VF_XY_LAYERS:  mrac_xy_layers = (uint8_t)(val + 0.5f); break;   /* flies after the ground latch */
        case MRAC_VF_REF_ZETA:   c->ref_model_zeta = val; break;
        case MRAC_VF_ATT_LAYERS: mrac_att_layers = (uint8_t)(val + 0.5f); break;   /* fly after the ground latch */
        case MRAC_VF_YAW_LAYERS: mrac_yaw_layers = (uint8_t)(val + 0.5f); break;
        case MRAC_VF_Z_LAYERS:   mrac_z_layers = (uint8_t)(val + 0.5f); break;
        default: return 0U;
    }
    /* Gate W: remember an explicit write of a reference field on the x / y axes, so the row selection on the
     * ground latch does not overwrite it. The record is per axis AND per field: a write on x never stops the row
     * from filling y, and a write of the bandwidth never stops the row from filling the damping. */
    if (axis == (uint8_t)MRAC_AXIS_X || axis == (uint8_t)MRAC_AXIS_Y) {
        uint8_t bit = 0U;
        switch (field) {
            case MRAC_VF_REF_TYPE:     bit = MRAC_XY_REF_W_TYPE;  break;
            case MRAC_VF_REF_MODEL_BW: bit = MRAC_XY_REF_W_BW;    break;
            case MRAC_VF_REF_ZETA:     bit = MRAC_XY_REF_W_ZETA;  break;
            case MRAC_VF_REF_DELAY_S:  bit = MRAC_XY_REF_W_DELAY; break;
            default: break;
        }
        if (bit != 0U) {
            uint8_t slot = (axis == (uint8_t)MRAC_AXIS_X) ? (uint8_t)MRAC_XY_REF_W_X : (uint8_t)MRAC_XY_REF_W_Y;
            mrac_xy_ref_written[slot] = (uint8_t)(mrac_xy_ref_written[slot] | bit);
        }
    }
    if (mrac_var_field[field].snap) {
        st[axis]->xm = st[axis]->x;
        st[axis]->xm_dot = 0.0f;
        MRAC_VariantSnap(st[axis]);
    }
    return 1U;
}

/* CMD 0x1D idx -> (axis, field). idx < 0x80: (field << 2) | axis, axes 0-3 (every sender before x/y);
 * idx >= 0x80: 0x80 | (field << 1) | (axis - 4), axes 4 x / 5 y. The old decode put every idx >= 0x80 at
 * field >= 32 (always refused), so no existing sender changes meaning. */
uint8_t MRAC_VariantIdxSet(uint8_t idx, float val)
{
    uint32_t i = (uint32_t)idx;
    uint8_t axis = ((i & 0x80U) != 0U) ? (uint8_t)((uint32_t)MRAC_AXIS_X + (i & 0x01U)) : (uint8_t)(i & 0x03U);
    uint8_t field = ((i & 0x80U) != 0U) ? (uint8_t)((i >> 1U) & 0x3FU) : (uint8_t)(i >> 2U);

    return MRAC_VariantParamSet(axis, field, val);
}

void MRAC_Reset(void)
{
    MRAC_ResetWeights();
}

// Experiment telemetry block (mrac.h). Pattern fields come from the task (MRAC_XtSetPattern), the rest is
// filled here once per tick, after the axis updates.

static int16_t MRAC_XtQ(float v, float scale)
{
    float q = v * scale;
    if (!(q > -32767.0f)) return (int16_t)-32767;   /* NaN reads as the low rail */
    if (q > 32767.0f) return (int16_t)32767;
    return (int16_t)(q < 0.0f ? q - 0.5f : q + 0.5f);
}

void MRAC_XtSetPattern(uint16_t pat, uint16_t phase, uint16_t period)
{
    mrac_xt.pat = (int16_t)pat;
    mrac_xt.phase = (int16_t)phase;
    mrac_xt.period = (int16_t)period;
}

static void MRAC_XtUpdate(void)
{
    const float k = MRAC_DT / (MRAC_XT_TAU_S + MRAC_DT);
    float sum[MRAC_N_GROUPS];
    int a, g, i, n, w;
    for (a = 0; a < AXES; a++) {
        const MRAC_AxisState_t *st = mrac_st_of[a];
        mrac_xt.e[a] = MRAC_XtQ(st->e, MRAC_XT_E_SCALE);
        mrac_xt.u_ad[a] = MRAC_XtQ(st->u_ad, MRAC_XT_U_SCALE);
        for (w = 0; w < MRAC_XT_TRAP_WORDS; w++) {
            uint16_t bits = 0U;
            for (i = 16 * w; i < 16 * w + 16 && i < MRAC_N_FEATURES; i++)
                if (mrac_l2g_trapped[a][i] != 0U) bits |= (uint16_t)(1U << (unsigned)(i - 16 * w));
            mrac_xt.trap[a][w] = (int16_t)bits;
        }
        for (g = 0; g < MRAC_N_GROUPS; g++) sum[g] = 0.0f;
        (void)MRAC_FeatureCount(mrac_cfg_of[a], &n);
        for (i = 0; i < n; i++) {
            g = mrac_feature_desc[i].group;
            sum[g] += st->Theta[i] * (st->Phi[i] * mrac_g_phi[a][g]);
        }
        for (g = 0; g < MRAC_N_GROUPS; g++) {
            mrac_xt_grp_f[a][g] += k * (sum[g] - mrac_xt_grp_f[a][g]);
            mrac_xt.grp[a][g] = MRAC_XtQ(mrac_xt_grp_f[a][g], MRAC_XT_U_SCALE);
        }
    }
}

void MRAC_Control(const CtrlerTypeDef* current_state)
{
    float p_rate, q_rate, r_rate;
    float cross_pitch, cross_roll;
    float r_pitch, r_roll, r_yaw, r_z, r_x, r_y;
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
    p_rate = current_state->gyroyPID.FB * MRAC_DEG2RAD; // Y-axis gyro represents Pitch
    q_rate = current_state->gyroxPID.FB * MRAC_DEG2RAD; // X-axis gyro represents Roll
    r_rate = current_state->gyrozPID.FB * MRAC_DEG2RAD; // Z-axis gyro represents Yaw
    
    // Assign measured states
    mrac_state.pitch.x = p_rate;
    mrac_state.roll.x  = q_rate;
    mrac_state.yaw.x   = r_rate;
    mrac_state.z_rate.x = current_state->Z_ratePID.FB; // Z-axis velocity measurement from cascade
    mrac_in_h = current_state->Z_posPID.FB;             /* m, ToF height, for the Z features */
    mrac_state.x_vel.x  = current_state->locxsPID.FB * 0.01f;  /* cm/s -> m/s (100 Hz signal) */
    mrac_state.y_vel.x  = current_state->locysPID.FB * 0.01f;
    
    // Compute cross-coupling terms
    cross_pitch = q_rate * r_rate;
    cross_roll  = p_rate * r_rate;
    
    // 2. Read current commanded targets streaming from outer loops
    // Degrees/s to Radians/s (for gyro references)
    r_pitch = current_state->gyroyPID.Des * MRAC_DEG2RAD;
    r_roll  = current_state->gyroxPID.Des * MRAC_DEG2RAD;
    r_yaw   = current_state->gyrozPID.Des * MRAC_DEG2RAD;
    r_z     = current_state->Z_ratePID.Des; // Pull desired vertical rate directly
    r_x     = current_state->locxsPID.Des * 0.01f;
    r_y     = current_state->locysPID.Des * 0.01f;
    
    // 3. Assign Nominal Control (U) computed by PID controllers
    // Convert from PWM/Mixer units down to physical torque/thrust SI Units 
    // to prevent regressor norm scaling explosion (1 + ||Phi||^2)
    mrac_state.pitch.u_nom  = current_state->gyroyPID.U / mrac_config_pitch.mrac_to_mixer;
    mrac_state.roll.u_nom   = current_state->gyroxPID.U / mrac_config_roll.mrac_to_mixer;
    mrac_state.yaw.u_nom    = current_state->gyrozPID.U / mrac_config_yaw.mrac_to_mixer;
    mrac_state.z_rate.u_nom = current_state->Z_ratePID.U / mrac_config_z.mrac_to_mixer;
    mrac_state.x_vel.u_nom  = mrac_xy_unom[0] / mrac_config_x.mrac_to_mixer;   /* nominal (PID or xy_ctrl), no u_ad */
    mrac_state.y_vel.u_nom  = mrac_xy_unom[1] / mrac_config_y.mrac_to_mixer;
    
    // 4. Perform inverse mixing to measure actual actuation capabilities
    // (Omitted in early dev, rely on basic limits)
    
    // 5-8. Update core MRAC algorithms for all axes (behind the input guard)
    if (mrac_flags.axis_enable_pitch) {
        MRAC_GuardedUpdate(MRAC_AXIS_PITCH, &mrac_state.pitch, &mrac_config_pitch, cross_pitch, r_pitch);
    } else {
        mrac_state.pitch.u_ad = 0.0f;
    }
    if (mrac_flags.axis_enable_roll) {
        MRAC_GuardedUpdate(MRAC_AXIS_ROLL,  &mrac_state.roll,  &mrac_config_roll,  cross_roll,  r_roll);
    } else {
        mrac_state.roll.u_ad = 0.0f;
    }
    if (mrac_flags.axis_enable_yaw) {
        MRAC_GuardedUpdate(MRAC_AXIS_YAW,   &mrac_state.yaw,   &mrac_config_yaw,   0.0f,        r_yaw);
    } else {
        mrac_state.yaw.u_ad = 0.0f;
    }
    MRAC_GuardedUpdate(MRAC_AXIS_Z,     &mrac_state.z_rate,&mrac_config_z,     0.0f,        r_z);
    if (mrac_flags.axis_enable_x && ((unsigned)mrac_xy_layers_live & MRAC_XY_LAYER_INNER) != 0U) {
        MRAC_GuardedUpdate(MRAC_AXIS_X,   &mrac_state.x_vel, &mrac_config_x,     0.0f,        r_x);
    } else {
        mrac_state.x_vel.u_ad = 0.0f;
    }
    if (mrac_flags.axis_enable_y && ((unsigned)mrac_xy_layers_live & MRAC_XY_LAYER_INNER) != 0U) {
        MRAC_GuardedUpdate(MRAC_AXIS_Y,   &mrac_state.y_vel, &mrac_config_y,     0.0f,        r_y);
    } else {
        mrac_state.y_vel.u_ad = 0.0f;
    }

    MRAC_XtUpdate();
}

// ------------------------------------------------------------------------------
// Opt-in sigma-prior accessors (prior-D / ADR-0013 D5, D10)
// ------------------------------------------------------------------------------
// Critical-section protected against the 200 Hz task-context MRAC_UpdateAxis read of
// Theta_prior[axis_id][:]. The ground-station command dispatch is the
// intended writer; MRAC_Control/UpdateAxis is the reader. A single-writer /
// single-reader pair is race-free under a PRIMASK critical section.
// ---------------------------------------------------------------------------
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

/* One element of Theta_prior[axis][]; 0 (nothing written) for a bad axis or index or a non-finite value, else 1. */
int MRAC_SetPriorElem(uint8_t axis, uint8_t idx, float v)
{
    uint32_t pri;

    if ((axis >= (uint8_t)AXES) || (idx >= (uint8_t)MRAC_N_FEATURES) || !isfinite(v)) {
        return 0;
    }
    pri = __get_PRIMASK();
    __disable_irq();
    Theta_prior[axis][idx] = v;
    __set_PRIMASK(pri);
    return 1;
}

/* Warm start: Theta = Whatf = Theta_prior, clamped to the projection set, on the axes in mrac_prior_warm, so the
 * axis starts at the prior equilibrium (sigma_lf term and u_pr zero). Called from MRAC_ResetWeights after the zero
 * loop (disarm edge, injection rising edge, MRAC_Reset); mrac_prior_warm = 0 (the default) leaves the zeroed weights. */
void MRAC_PriorWarmStart(void)
{
    uint8_t axis;
    int i;

    for (axis = 0U; axis < (uint8_t)AXES; axis++) {
        const MRAC_AxisConfig_t *cfg = mrac_cfg_of[axis];
        MRAC_AxisState_t *st = mrac_st_of[axis];

        if ((((unsigned)mrac_prior_warm >> (unsigned)axis) & 1U) == 0U) {
            continue;
        }
        for (i = 0; i < MAX_NUM_BASIS; i++) {
            float v = Theta_prior[axis][i];

            if (v > cfg->What_limit[i]) {
                v = cfg->What_limit[i];
            } else if (v < cfg->What_lower_limit[i]) {
                v = cfg->What_lower_limit[i];
            }
            st->Theta[i] = v;
            st->Whatf[i] = v;
        }
    }
}

void MRAC_ZFeatOut(float feat[MRAC_ZFEAT_N], float w[MRAC_ZFEAT_N])
{
    int k;

    for (k = 0; k < MRAC_ZFEAT_N; k++) {
#if MRAC_VARIANT == MRAC_VARIANT_MULTI
        feat[k] = mrac_state.z_rate.Phi[MRAC_N_STRUCT + k];
        w[k] = mrac_state.z_rate.Theta[MRAC_N_STRUCT + k];
#else
        feat[k] = 0.0f;
        w[k] = 0.0f;
#endif
    }
}
