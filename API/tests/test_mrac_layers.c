#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

#include "mrac.h"
#include "robot_types.h"

_imu_st imu_data = {0};

static void pass(const char* name) {
    printf("PASS %s\n", name);
}

static void fail(const char* name) {
    printf("FAIL %s\n", name);
    exit(1);
}

static void check_identity(void) {
    char filename[64];
#if MRAC_VARIANT == 0 || MRAC_VARIANT == 5
    strcpy(filename, "trace_v0_v5.bin");
#elif MRAC_VARIANT == 4 || MRAC_VARIANT == 6
    strcpy(filename, "trace_v4_v6.bin");
#else
    return;
#endif

    FILE *f = NULL;
#if MRAC_VARIANT == 0 || MRAC_VARIANT == 4
    f = fopen(filename, "wb");
#else
    f = fopen(filename, "rb");
    mrac_layer_sel.l2_on = 0;
    mrac_layer_sel.l3_on = 0;
#endif
    if (!f) fail("identity_file");

    MRAC_Init();
    mrac_flags.adaptation_on = 1;
    mrac_flags.projection_on = 1;
    mrac_flags.output_injection_on = 1;
    mrac_flags.axis_enable_pitch = 1;

    float plant_x = 0.0f;
    int ok = 1;
    (void)ok;
    for (int tick = 0; tick < 4000; tick++) {
        float r = ((tick / 200) % 2 == 0) ? 1.0f : -1.0f;
        CtrlerTypeDef ctrl_cl;
        memset(&ctrl_cl, 0, sizeof(ctrl_cl));
        ctrl_cl.gyroyPID.Des = r / 0.0174533f;
        ctrl_cl.gyroyPID.FB = plant_x / 0.0174533f;

        float err = r - plant_x;
        float u_nom = 2.0f * err;
        ctrl_cl.gyroyPID.U = u_nom * mrac_config_pitch.mrac_to_mixer;

        MRAC_Control(&ctrl_cl);
        float u_ad = mrac_state.pitch.u_ad;
        float u_out = 0.0f;
#if MRAC_VARIANT == 5 || MRAC_VARIANT == 6
        u_out = MRAC_GetOutput(MRAC_AXIS_PITCH);
#else
        u_out = u_ad;
#endif
        
        float u_tot = u_nom + u_ad;
        float x_dot = 10.0f * u_tot - 5.0f * plant_x - 2.0f * plant_x * fabsf(plant_x);
        plant_x += x_dot * MRAC_DT;

        struct { float u_ad, u_out; float theta[MRAC_N_FEATURES]; } state;
        state.u_ad = u_ad;
        state.u_out = u_out;
        memcpy(state.theta, mrac_state.pitch.Theta, sizeof(state.theta));

#if MRAC_VARIANT == 0 || MRAC_VARIANT == 4
        fwrite(&state, sizeof(state), 1, f);
#else
        struct { float u_ad, u_out; float theta[MRAC_N_FEATURES]; } ref;
        if (fread(&ref, sizeof(ref), 1, f) != 1) { ok = 0; break; }
        
        uint32_t act_u, exp_u;
        memcpy(&act_u, &state.u_ad, 4);
        memcpy(&exp_u, &ref.u_ad, 4);
        if (act_u != exp_u) ok = 0;
        
        memcpy(&act_u, &state.u_out, 4);
        memcpy(&exp_u, &ref.u_out, 4);
        if (act_u != exp_u) ok = 0;

        for (int i = 0; i < MRAC_N_FEATURES; i++) {
            memcpy(&act_u, &state.theta[i], 4);
            memcpy(&exp_u, &ref.theta[i], 4);
            if (act_u != exp_u) ok = 0;
        }
#endif
    }
    fclose(f);

#if MRAC_VARIANT == 5 || MRAC_VARIANT == 6
    if (ok) pass("identity"); else fail("identity");
#endif
}

#if MRAC_VARIANT == 5 || MRAC_VARIANT == 6
static void check_iir_bank(void) {
    if (MRAC_VARIANT != 5) return;
    int ok = 1;
    float band_fc[4] = {1.0f, 4.0f, 10.0f, 25.0f};
    for (int b = 0; b < 4; b++) {
        MRAC_Init();
        mrac_layer_sel.l2_on = 1;
        mrac_layer_sel.l2_input = MRAC_L2_IN_REF;
        float f_c = band_fc[b];
        float w = 2.0f * 3.1415926535f * f_c;
        for (int i = 0; i < 2000; i++) {
            float t = i * MRAC_DT;
            CtrlerTypeDef ctrl;
            memset(&ctrl, 0, sizeof(ctrl));
            ctrl.gyroyPID.Des = sinf(w * t) / 0.0174533f;
            MRAC_Control(&ctrl);
        }
        float e_b = mrac_l2_energy[MRAC_AXIS_PITCH][b];
        if (fabsf(e_b - 0.5f) > 0.05f) ok = 0;
        for (int j = 0; j < 4; j++) {
            if (j != b && mrac_l2_energy[MRAC_AXIS_PITCH][j] > e_b) ok = 0;
        }
    }
    
    MRAC_Init();
    mrac_layer_sel.l2_on = 1;
    mrac_layer_sel.l2_input = MRAC_L2_IN_REF;
    float w_high = 2.0f * 3.1415926535f * 90.0f;
    for (int i = 0; i < 2000; i++) {
        float t = i * MRAC_DT;
        CtrlerTypeDef ctrl;
        memset(&ctrl, 0, sizeof(ctrl));
        ctrl.gyroyPID.Des = sinf(w_high * t) / 0.0174533f;
        MRAC_Control(&ctrl);
    }
    for (int j = 0; j < 4; j++) {
        if (mrac_l2_energy[MRAC_AXIS_PITCH][j] > 0.2f) ok = 0;
    }
    if (ok) pass("iir_bank"); else fail("iir_bank");
}

static void check_normalisation(void) {
    if (MRAC_VARIANT != 5) return;
    int ok = 1;
    float sum = 0.0f;
    int n_used = 0;
    int has_f[8] = {0};
    
    MRAC_Init();
    mrac_layer_sel.l2_on = 1;
    mrac_layer_sel.l2_input = MRAC_L2_IN_REF;
    for (int i = 0; i < 500; i++) {
        CtrlerTypeDef ctrl;
        memset(&ctrl, 0, sizeof(ctrl));
        ctrl.gyroyPID.Des = (sinf(2.0f * 3.1415926f * 4.0f * i * MRAC_DT) + sinf(2.0f * 3.1415926f * 10.0f * i * MRAC_DT)) / 0.0174533f;
        
        mrac_layer_sel.l2_norm = MRAC_L2_NORM_NONE;
        mrac_layer_sel.l2_out_mask = MRAC_L2_OUT_GAMMA;
        for(int k=0;k<4;k++) MRAC_Control(&ctrl);
        for(int g=0;g<MRAC_N_GROUPS;g++) if (mrac_g_gamma[MRAC_AXIS_PITCH][g] != 1.0f) ok = 0;
        
        mrac_layer_sel.l2_norm = MRAC_L2_NORM_INDEP;
        for(int k=0;k<4;k++) MRAC_Control(&ctrl);
        for(int g=0;g<MRAC_N_GROUPS;g++) {
            if (mrac_g_gamma[MRAC_AXIS_PITCH][g] < 0.0f || mrac_g_gamma[MRAC_AXIS_PITCH][g] > 1.0f) ok = 0;
        }
        
        mrac_layer_sel.l2_norm = MRAC_L2_NORM_MEAN;
        for(int k=0;k<4;k++) MRAC_Control(&ctrl);
        sum = 0.0f; n_used = 0;
        memset(has_f, 0, sizeof(has_f));
        for(int f=0; f<MRAC_N_FEATURES; f++) has_f[mrac_feature_desc[f].group] = 1;
        for(int g=0;g<MRAC_N_GROUPS;g++) if (has_f[g]) { sum += mrac_g_gamma[MRAC_AXIS_PITCH][g]; n_used++; }
        
        mrac_layer_sel.l2_norm = MRAC_L2_NORM_SOFTMAX;
        for(int k=0;k<4;k++) MRAC_Control(&ctrl);
        sum = 0.0f; n_used = 0;
        memset(has_f, 0, sizeof(has_f));
        for(int f=0; f<MRAC_N_FEATURES; f++) has_f[mrac_feature_desc[f].group] = 1;
        for(int g=0;g<MRAC_N_GROUPS;g++) if (has_f[g]) { sum += mrac_g_gamma[MRAC_AXIS_PITCH][g]; n_used++; }
        if (n_used > 0 && fabsf(sum - n_used) > 1e-3f) ok = 0;
    }
    
    if (ok) pass("normalisation"); else fail("normalisation");
}

static void check_outputs(void) {
    if (MRAC_VARIANT != 5) return;
    int ok = 1;
    MRAC_Init();
    mrac_layer_sel.l2_on = 1;
    mrac_layer_sel.l2_input = MRAC_L2_IN_REF;
    
    CtrlerTypeDef ctrl;
    memset(&ctrl, 0, sizeof(ctrl));
    ctrl.gyroyPID.Des = 10.0f / 0.0174533f;
    for(int i=0; i<100; i++) MRAC_Control(&ctrl);
    
    mrac_layer_sel.l2_out_mask = MRAC_L2_OUT_GAMMA;
    for(int k=0;k<4;k++) MRAC_Control(&ctrl);
    for(int g=0;g<MRAC_N_GROUPS;g++) {
        if (mrac_g_sigma[MRAC_AXIS_PITCH][g] != 1.0f) ok = 0;
        if (mrac_g_phi[MRAC_AXIS_PITCH][g] != 1.0f) ok = 0;
    }
    
    for(int i=0; i<4; i++) mrac_l2_energy[MRAC_AXIS_PITCH][i] = 0.0f;
    mrac_l2_energy[MRAC_AXIS_PITCH][3] = 1.0f;
    mrac_layer_sel.l2_norm = MRAC_L2_NORM_INDEP;
    mrac_layer_sel.l2_out_mask = MRAC_L2_OUT_PHI;
    memset(&ctrl, 0, sizeof(ctrl));
    for(int k=0;k<4;k++) MRAC_Control(&ctrl);
    if (mrac_g_gamma[MRAC_AXIS_PITCH][MRAC_GRP_BIAS] != 0.0f) ok = 0;
    
    if (ok) pass("outputs"); else fail("outputs");
}

static void check_energy_floor(void) {
    if (MRAC_VARIANT != 5) return;
    int ok = 1;
    MRAC_Init();
    mrac_layer_sel.l2_on = 1;
    mrac_layer_sel.l2_input = MRAC_L2_IN_REF;
    mrac_layer_sel.l2_norm = MRAC_L2_NORM_INDEP;
    mrac_layer_sel.l2_out_mask = MRAC_L2_OUT_GAMMA | MRAC_L2_OUT_PHI | MRAC_L2_OUT_SIGMA;
    
    for (int i = 0; i < 2000; i++) {
        CtrlerTypeDef ctrl;
        memset(&ctrl, 0, sizeof(ctrl));
        MRAC_Control(&ctrl);
    }
    
    for(int g=0;g<MRAC_N_GROUPS;g++) {
        if (mrac_g_gamma[MRAC_AXIS_PITCH][g] != 1.0f) ok = 0;
        if (mrac_g_phi[MRAC_AXIS_PITCH][g] != 1.0f) ok = 0;
    }
    if (ok) pass("energy_floor"); else fail("energy_floor");
}

static void check_divider(void) {
    if (MRAC_VARIANT != 5) return;
    int ok = 1;
    MRAC_Init();
    mrac_layer_sel.l2_on = 1;
    float old_g = mrac_g_gamma[MRAC_AXIS_PITCH][0];
    for (int i = 1; i <= 10; i++) {
        CtrlerTypeDef ctrl;
        memset(&ctrl, 0, sizeof(ctrl));
        ctrl.gyroyPID.Des = i * 10.0f;
        MRAC_Control(&ctrl);
        if (i % 4 != 0) {
            if (mrac_g_gamma[MRAC_AXIS_PITCH][0] != old_g) ok = 0;
        } else {
            old_g = mrac_g_gamma[MRAC_AXIS_PITCH][0];
        }
    }
    if (ok) pass("divider"); else fail("divider");
}

static void check_l3(void) {
    if (MRAC_VARIANT != 5) return;
    int ok = 1;
    MRAC_Init();
    mrac_layer_sel.l3_on = 1;
    
    mrac_layer_sel.l3_src = MRAC_L3_SRC_XMDOT;
    CtrlerTypeDef ctrl;
    memset(&ctrl, 0, sizeof(ctrl));
    ctrl.gyroyPID.Des = 1.0f / 0.0174533f;
    MRAC_Control(&ctrl);
    float xm_dot = mrac_state.pitch.xm_dot;
    float u_ff = MRAC_GetOutput(MRAC_AXIS_PITCH) - mrac_state.pitch.u_ad;
    float expected_u_ff = mrac_l3_cfg[MRAC_AXIS_PITCH].k_ff * xm_dot;
    if (fabsf(u_ff - expected_u_ff) > 1e-6f && fabsf(u_ff) < mrac_l3_cfg[MRAC_AXIS_PITCH].u_ff_max) ok = 0;
    
    MRAC_Init();
    mrac_layer_sel.l3_on = 1;
    mrac_layer_sel.l3_src = MRAC_L3_SRC_RDOT;
    float slope = 2.0f;
    for (int i = 0; i < 500; i++) {
        float r = i * MRAC_DT * slope;
        memset(&ctrl, 0, sizeof(ctrl));
        ctrl.gyroyPID.Des = r / 0.0174533f;
        MRAC_Control(&ctrl);
        if (i * MRAC_DT >= 5.0f * mrac_l3_cfg[MRAC_AXIS_PITCH].tau_d) {
            u_ff = MRAC_GetOutput(MRAC_AXIS_PITCH) - mrac_state.pitch.u_ad;
            expected_u_ff = mrac_l3_cfg[MRAC_AXIS_PITCH].k_ff * slope;
            if (fabsf(u_ff - expected_u_ff) > 0.02f * expected_u_ff) ok = 0;
        }
    }
    memset(&ctrl, 0, sizeof(ctrl));
    ctrl.gyroyPID.Des = 1.0f / 0.0174533f;
    for (int i = 0; i < 500; i++) MRAC_Control(&ctrl);
    u_ff = MRAC_GetOutput(MRAC_AXIS_PITCH) - mrac_state.pitch.u_ad;
    if (fabsf(u_ff) > 1e-4f) ok = 0;
    
    if (ok) pass("l3"); else fail("l3");
}

static void check_select_latch(void) {
    if (MRAC_VARIANT != 5) return;
    int ok = 1;
    MRAC_Init();
    mrac_layer_sel.l2_on = 0;
    mrac_layer_sel_req.l2_on = 1;
    MRAC_LayerSelectStep(1);
    if (mrac_layer_sel.l2_on != 0) ok = 0;
    
    MRAC_LayerSelectStep(0);
    if (mrac_layer_sel.l2_on != 1) ok = 0;
    
    mrac_layer_sel_req.l2_norm = 100;
    MRAC_LayerSelectStep(0);
    if (mrac_layer_sel_req.l2_norm == 100) ok = 0;
    
    if (ok) pass("select_latch"); else fail("select_latch");
}

static void check_closed_loop(void) {
    int ok = 1;
    MRAC_Init();
    mrac_layer_sel.l2_on = 1;
    mrac_layer_sel.l3_on = 1;
    mrac_layer_sel.l2_input = MRAC_L2_IN_REF;
    mrac_layer_sel.l2_norm = MRAC_L2_NORM_SOFTMAX;
    mrac_layer_sel.l2_out_mask = MRAC_L2_OUT_GAMMA | MRAC_L2_OUT_PHI | MRAC_L2_OUT_SIGMA;
    mrac_layer_sel.l3_src = MRAC_L3_SRC_XMDOT;
    
    mrac_flags.adaptation_on = 1;
    mrac_flags.projection_on = 1;
    mrac_flags.output_injection_on = 1;
    mrac_flags.axis_enable_pitch = 1;

    float plant_x = 0.0f;
    for (int tick = 0; tick < 4000; tick++) {
        float r = ((tick / 200) % 2 == 0) ? 1.0f : -1.0f;
        float dist = 0.5f * sinf(2.0f * 3.1415926f * 2.0f * tick * MRAC_DT);
        
        CtrlerTypeDef ctrl_cl;
        memset(&ctrl_cl, 0, sizeof(ctrl_cl));
        ctrl_cl.gyroyPID.Des = r / 0.0174533f;
        ctrl_cl.gyroyPID.FB = plant_x / 0.0174533f;

        float err = r - plant_x;
        float u_nom = 2.0f * err;
        ctrl_cl.gyroyPID.U = u_nom * mrac_config_pitch.mrac_to_mixer;

        MRAC_Control(&ctrl_cl);
        float u_out = MRAC_GetOutput(MRAC_AXIS_PITCH);
        
        float u_tot = u_nom + u_out;
        float x_dot = 10.0f * u_tot - 5.0f * plant_x - 2.0f * plant_x * fabsf(plant_x) + dist;
        plant_x += x_dot * MRAC_DT;

        if (isnan(plant_x) || isinf(plant_x)) ok = 0;
        if (isnan(u_out) || isinf(u_out)) ok = 0;

        if (fabsf(u_out) > mrac_config_pitch.u_max + 1e-4f) ok = 0;

        for (int i = 0; i < MRAC_N_FEATURES; i++) {
            float w = mrac_state.pitch.Theta[i];
            float lim = mrac_config_pitch.What_limit[i];
            float low = mrac_config_pitch.What_lower_limit[i];
            if (w > lim + 1e-4f || w < low - 1e-4f) ok = 0;
        }
        for (int g = 0; g < MRAC_N_GROUPS; g++) {
            if (mrac_g_gamma[MRAC_AXIS_PITCH][g] < 0.0f || mrac_g_gamma[MRAC_AXIS_PITCH][g] > 8.0f) ok = 0;
            if (mrac_g_phi[MRAC_AXIS_PITCH][g] < 0.0f || mrac_g_phi[MRAC_AXIS_PITCH][g] > 8.0f) ok = 0;
            if (mrac_g_sigma[MRAC_AXIS_PITCH][g] < 0.0f || mrac_g_sigma[MRAC_AXIS_PITCH][g] > 1.0f) ok = 0;
        }
    }
    if (ok) pass("closed_loop"); else fail("closed_loop");
}
#endif

int main(void) {
    check_identity();

#if MRAC_VARIANT == 5 || MRAC_VARIANT == 6
    check_iir_bank();
    check_normalisation();
    check_outputs();
    check_energy_floor();
    check_divider();
    check_l3();
    check_select_latch();
    check_closed_loop();

    printf("SIZEOF mrac_l2_state: %zu\n", sizeof(mrac_l2_state));
    printf("SIZEOF mrac_l2_energy: %zu\n", sizeof(mrac_l2_energy));
    printf("SIZEOF mrac_l3_cfg: %zu\n", sizeof(mrac_l3_cfg));
#endif

    return 0;
}
