#ifndef MRAC_LAYERS_H
#define MRAC_LAYERS_H

#if MRAC_L2_MODE != 0 || MRAC_L3_MODE != 0

/* L2 Configuration */
typedef char MRAC_L2_Assert_Bands[(MRAC_L2_N_BANDS >= 1 && MRAC_L2_N_BANDS <= 8) ? 1 : -1];

/* PROVISIONAL log-spaced centres below 40 Hz: chosen, no source */
typedef struct {
    float f_c;
    float q;
} MRAC_L2_BandCfg_t;

static const MRAC_L2_BandCfg_t l2_band_cfg[MRAC_L2_N_BANDS] = {
    {1.0f, 1.0f},
    {4.0f, 1.0f},
    {10.0f, 1.0f},
    {25.0f, 1.0f}
};

/* PROVISIONAL L2 MAP table */
typedef struct {
    float sigma_scale;
    float m[MRAC_L2_N_BANDS];
} MRAC_L2_MapRow_t;

static const MRAC_L2_MapRow_t l2_map[8] = {
    /* BIAS     */ {1.0f, {1.0f, 0.0f, 0.0f, 0.0f}},
    /* RATE     */ {1.0f, {0.0f, 0.0f, 1.0f, 1.0f}},
    /* AERO     */ {1.0f, {1.0f, 1.0f, 0.0f, 0.0f}},
    /* COUPLING */ {1.0f, {0.0f, 1.0f, 1.0f, 0.0f}},
    /* CTRL     */ {1.0f, {0.0f, 0.0f, 1.0f, 1.0f}},
    /* REF      */ {1.0f, {1.0f, 1.0f, 0.0f, 0.0f}},
    /* RBF      */ {1.0f, {1.0f, 1.0f, 1.0f, 1.0f}},
    /* POLY     */ {1.0f, {1.0f, 1.0f, 0.0f, 0.0f}}
};

typedef struct {
    float a1;
    float a2;
    float b0;
    float b1;
    float b2;
    float alpha_e;
} MRAC_L2_BandCoeff_t;

MRAC_CCM MRAC_L2_BandState_t mrac_l2_state[AXES][MRAC_L2_N_BANDS];
MRAC_CCM float mrac_l2_energy[AXES][MRAC_L2_N_BANDS];
static MRAC_L2_BandCoeff_t l2_coeff[MRAC_L2_N_BANDS];

/* PROVISIONAL constants */
#define MRAC_L2_G_MIN 0.0f
#define MRAC_L2_G_MAX 1.0f
#define MRAC_L2_T 1.0f
#define MRAC_L2_PHI_FREEZE 0.01f
#define MRAC_L2_E_FLOOR 1e-4f
#define MRAC_L2_EPS 1e-6f
#define MRAC_L2_TAU_E 0.5f

#ifndef MRAC_L2_GATE_DIV
#define MRAC_L2_GATE_DIV 4
#endif

/* L3 Configuration */
/* k_ff = inertia from sim/adaptive_compare/sim_coupled.py. Initialized, so not MRAC_CCM (zero_init: armcc #145). */
MRAC_L3_Cfg_t mrac_l3_cfg[AXES] = {
    {0.0023f, 0.05f, 0.2f},
    {0.0023f, 0.05f, 0.2f},
    {0.0015f, 0.05f, 0.2f},
    {0.0f,    0.05f, 0.0f}
};

static MRAC_CCM float mrac_l3_d[AXES];
static MRAC_CCM float mrac_l3_r_prev[AXES];
static MRAC_CCM uint32_t l2_tick_counter; /* zeroed by __main (zero_init) */

static void MRAC_Layers_Init(void) {
    int i, a;
    float w0, alpha, a0;
    for (i = 0; i < MRAC_L2_N_BANDS; i++) {
        w0 = 2.0f * 3.1415926535f * l2_band_cfg[i].f_c * MRAC_DT;
        alpha = sinf(w0) / (2.0f * l2_band_cfg[i].q);
        a0 = 1.0f + alpha;
        l2_coeff[i].b0 = alpha / a0;
        l2_coeff[i].b1 = 0.0f;
        l2_coeff[i].b2 = -alpha / a0;
        l2_coeff[i].a1 = -2.0f * cosf(w0) / a0;
        l2_coeff[i].a2 = (1.0f - alpha) / a0;
        l2_coeff[i].alpha_e = MRAC_DT / MRAC_L2_TAU_E;
    }
    for (a = 0; a < (int)AXES; a++) {
        mrac_l3_d[a] = 0.0f;
        mrac_l3_r_prev[a] = 0.0f;
        for (i = 0; i < MRAC_L2_N_BANDS; i++) {
            mrac_l2_state[a][i].z1 = 0.0f;
            mrac_l2_state[a][i].z2 = 0.0f;
            mrac_l2_energy[a][i] = 0.0f;
        }
    }
}

static float L2_GetInput(MRAC_Axis_e axis) {
    const MRAC_AxisConfig_t *cfg;
    if (axis == MRAC_AXIS_PITCH) cfg = &mrac_config_pitch;
    else if (axis == MRAC_AXIS_ROLL) cfg = &mrac_config_roll;
    else if (axis == MRAC_AXIS_YAW) cfg = &mrac_config_yaw;
    else cfg = &mrac_config_z;
    
    switch (mrac_layer_sel.l2_input) {
        case MRAC_L2_IN_ERR: return mrac_bus[axis].e;
        case MRAC_L2_IN_GYRO: return mrac_bus[axis].x;
        case MRAC_L2_IN_REF: return mrac_bus[axis].r;
        case MRAC_L2_IN_UAD:
            if (axis == MRAC_AXIS_PITCH) return mrac_state.pitch.u_ad;
            if (axis == MRAC_AXIS_ROLL) return mrac_state.roll.u_ad;
            if (axis == MRAC_AXIS_YAW) return mrac_state.yaw.u_ad;
            return mrac_state.z_rate.u_ad;
        case MRAC_L2_IN_RESID:
            return mrac_bus[axis].e_dot + cfg->ref_model_bw * mrac_bus[axis].e; /* a_m = ref_model_bw at API/mrac.h:122 */
        default: return 0.0f;
    }
}

static void MRAC_L2_Update(void) {
    int a, k, g;
    float sum_e, p_k, s_g, a_g;
    float y, x_in;
    float s_arr[8];
    float exp_s[8];
    float sum_exp = 0.0f;
    float sum_s = 0.0f;
    int n_used = 0;
    int has_feature[8] = {0};
    int f;
    
    if (!mrac_layer_sel.l2_on) return;

    /* Filter step */
    for (a = 0; a < (int)AXES; a++) {
        x_in = L2_GetInput((MRAC_Axis_e)a);
        for (k = 0; k < MRAC_L2_N_BANDS; k++) {
            y = l2_coeff[k].b0 * x_in + mrac_l2_state[a][k].z1;
            mrac_l2_state[a][k].z1 = l2_coeff[k].b1 * x_in - l2_coeff[k].a1 * y + mrac_l2_state[a][k].z2;
            mrac_l2_state[a][k].z2 = l2_coeff[k].b2 * x_in - l2_coeff[k].a2 * y;
            mrac_l2_energy[a][k] += l2_coeff[k].alpha_e * (y * y - mrac_l2_energy[a][k]);
        }
    }

    l2_tick_counter++;
    if (l2_tick_counter % MRAC_L2_GATE_DIV != 0) return;

    /* Determine used groups */
    for (f = 0; f < MRAC_N_FEATURES; f++) {
        has_feature[mrac_feature_desc[f].group] = 1;
    }
    for (g = 0; g < MRAC_N_GROUPS; g++) {
        if (has_feature[g]) n_used++;
    }

    for (a = 0; a < (int)AXES; a++) {
        sum_e = 0.0f;
        for (k = 0; k < MRAC_L2_N_BANDS; k++) {
            sum_e += mrac_l2_energy[a][k];
        }

        if (sum_e < MRAC_L2_E_FLOOR) {
            for (g = 0; g < MRAC_N_GROUPS; g++) {
                mrac_g_gamma[a][g] = 1.0f;
                mrac_g_phi[a][g] = 1.0f;
                mrac_g_sigma[a][g] = l2_map[g].sigma_scale;
                if ((mrac_layer_sel.l2_out_mask & MRAC_L2_OUT_SIGMA) == 0) mrac_g_sigma[a][g] = 1.0f;
            }
            continue;
        }

        sum_exp = 0.0f;
        sum_s = 0.0f;
        for (g = 0; g < MRAC_N_GROUPS; g++) {
            s_arr[g] = 0.0f;
            if (!has_feature[g]) continue;
            for (k = 0; k < MRAC_L2_N_BANDS; k++) {
                p_k = mrac_l2_energy[a][k] / (sum_e + MRAC_L2_EPS);
                s_arr[g] += l2_map[g].m[k] * p_k;
            }
            sum_s += s_arr[g];
            exp_s[g] = expf(s_arr[g] / MRAC_L2_T);
            sum_exp += exp_s[g];
        }

        for (g = 0; g < MRAC_N_GROUPS; g++) {
            if (!has_feature[g]) {
                mrac_g_gamma[a][g] = 1.0f;
                mrac_g_phi[a][g] = 1.0f;
                mrac_g_sigma[a][g] = l2_map[g].sigma_scale;
                if ((mrac_layer_sel.l2_out_mask & MRAC_L2_OUT_SIGMA) == 0) mrac_g_sigma[a][g] = 1.0f;
                continue;
            }

            s_g = s_arr[g];
            a_g = 1.0f;
            switch (mrac_layer_sel.l2_norm) {
                case MRAC_L2_NORM_NONE:
                    a_g = 1.0f;
                    break;
                case MRAC_L2_NORM_INDEP:
                    a_g = MRAC_L2_G_MIN + (1.0f - MRAC_L2_G_MIN) * s_g;
                    break;
                case MRAC_L2_NORM_MEAN:
                    if (n_used > 0) {
                        float mean_s = sum_s / n_used;
                        if (mean_s > MRAC_L2_EPS) a_g = s_g / mean_s;
                    }
                    if (a_g < MRAC_L2_G_MIN) a_g = MRAC_L2_G_MIN;
                    if (a_g > MRAC_L2_G_MAX) a_g = MRAC_L2_G_MAX;
                    break;
                case MRAC_L2_NORM_SOFTMAX:
                    if (sum_exp > MRAC_L2_EPS) {
                        a_g = n_used * exp_s[g] / sum_exp;
                    }
                    break;
            }

            mrac_g_gamma[a][g] = 1.0f;
            mrac_g_phi[a][g] = 1.0f;
            mrac_g_sigma[a][g] = 1.0f;

            if (mrac_layer_sel.l2_out_mask & MRAC_L2_OUT_GAMMA) mrac_g_gamma[a][g] = a_g;
            if (mrac_layer_sel.l2_out_mask & MRAC_L2_OUT_PHI) {
                mrac_g_phi[a][g] = a_g;
                if (a_g < MRAC_L2_PHI_FREEZE) {
                    mrac_g_gamma[a][g] = 0.0f;
                }
            }
            if (mrac_layer_sel.l2_out_mask & MRAC_L2_OUT_SIGMA) mrac_g_sigma[a][g] = l2_map[g].sigma_scale;
        }
    }
}

static float MRAC_L3_Feedforward(MRAC_Axis_e axis, const MRAC_Bus_t* bus) {
    float u_ff = 0.0f;
    if (mrac_layer_sel.l3_src == MRAC_L3_SRC_XMDOT) {
        u_ff = mrac_l3_cfg[axis].k_ff * bus->xm_dot;
    } else if (mrac_layer_sel.l3_src == MRAC_L3_SRC_RDOT) {
        mrac_l3_d[axis] += (MRAC_DT / mrac_l3_cfg[axis].tau_d) * ((bus->r - mrac_l3_r_prev[axis]) / MRAC_DT - mrac_l3_d[axis]);
        u_ff = mrac_l3_cfg[axis].k_ff * mrac_l3_d[axis];
    }
    mrac_l3_r_prev[axis] = bus->r;
    
    if (u_ff > mrac_l3_cfg[axis].u_ff_max) u_ff = mrac_l3_cfg[axis].u_ff_max;
    if (u_ff < -mrac_l3_cfg[axis].u_ff_max) u_ff = -mrac_l3_cfg[axis].u_ff_max;
    
    return u_ff;
}

#endif /* MRAC_L2_MODE != 0 || MRAC_L3_MODE != 0 */

#endif /* MRAC_LAYERS_H */
