#include <stdio.h>
#include <math.h>
#include <string.h>
#include <stdlib.h>
#include "mrac.h"

_imu_st imu_data = {0};

void pass(const char* name) {
    printf("PASS %s\n", name);
}

void fail(const char* name) {
    printf("FAIL %s\n", name);
    exit(1);
}

/* Pitch axis in closed loop on a simple nonlinear rate plant, x_dot = 10 u - 5 x - 2 x |x|, with a square-wave
 * reference and a proportional nominal controller. With freeze_new_block the adaptation rate of every feature
 * after the struct block is set to 0. Returns 0 on NaN/Inf, |u_ad| above u_max or a weight outside its limits. */
static int run_closed_loop(int freeze_new_block) {
    int ok = 1;
    float plant_x = 0.0f;

    MRAC_Init();
    mrac_flags.adaptation_on = 1;
    mrac_flags.projection_on = 1;
    mrac_flags.output_injection_on = 1;
    mrac_flags.axis_enable_pitch = 1;
    if (freeze_new_block) {
        for (int i = MRAC_N_STRUCT; i < MRAC_N_FEATURES; i++) mrac_config_pitch.gamma[i] = 0.0f;
    }

    for (int tick = 0; tick < 4000; tick++) {
        float r = ((tick / 200) % 2 == 0) ? 1.0f : -1.0f; // square wave

        CtrlerTypeDef ctrl_cl;
        memset(&ctrl_cl, 0, sizeof(ctrl_cl));
        ctrl_cl.gyroyPID.Des = r / 0.0174533f;
        ctrl_cl.gyroyPID.FB = plant_x / 0.0174533f;

        // Simple P-controller for nominal
        float err = r - plant_x;
        float u_nom = 2.0f * err;
        ctrl_cl.gyroyPID.U = u_nom * mrac_config_pitch.mrac_to_mixer;

        MRAC_Control(&ctrl_cl);

        float u_tot = u_nom + mrac_state.pitch.u_ad;
        float x_dot = 10.0f * u_tot - 5.0f * plant_x - 2.0f * plant_x * fabsf(plant_x);
        plant_x += x_dot * MRAC_DT;

        if (isnan(plant_x) || isinf(plant_x)) ok = 0;
        if (isnan(mrac_state.pitch.u_ad) || isinf(mrac_state.pitch.u_ad)) ok = 0;

        if (fabsf(mrac_state.pitch.u_ad) > mrac_config_pitch.u_max + 1e-4f) ok = 0;

        for (int i = 0; i < MRAC_N_FEATURES; i++) {
            float w = mrac_state.pitch.Theta[i];
            float lim = mrac_config_pitch.What_limit[i];
            float low = mrac_config_pitch.What_lower_limit[i];
            if (w > lim + 1e-4f || w < low - 1e-4f) ok = 0;
        }
    }
    return ok;
}

/* Number of pitch weights in [first, last) that are not exactly 0. */
static int count_nonzero_theta(int first, int last) {
    int n = 0;
    for (int i = first; i < last; i++) {
        if (mrac_state.pitch.Theta[i] != 0.0f) n++;
    }
    return n;
}

int main() {
    MRAC_Init();
    
    // a. descriptor
    int desc_ok = 1;
    if (mrac_n_features != MRAC_N_FEATURES) desc_ok = 0;
    for (int i = 0; i < MRAC_N_FEATURES; i++) {
        if (mrac_feature_desc[i].index != i) desc_ok = 0;
        if (mrac_feature_desc[i].group >= MRAC_N_GROUPS) desc_ok = 0;
    }
    if (desc_ok) pass("descriptor"); else fail("descriptor");

    // b. RBF
#if MRAC_N_RBF > 0
    int rbf_ok = 1;
    float x_scale = 5.0f; // pitch/roll/yaw
    // we use pitch axis for test
    for (int step = 0; step < 9; step++) {
        float x = -2.0f * x_scale + step * (4.0f * x_scale / 8.0f);
        mrac_state.pitch.x = x;
        mrac_state.pitch.xm = 0;
        mrac_state.pitch.u_nom = 0;
        // Run update indirectly by setting PID values?
        // Wait, we can just call the generator manually, or call MRAC_Control.
        CtrlerTypeDef ctrl;
        memset(&ctrl, 0, sizeof(ctrl));
        ctrl.gyroyPID.FB = x / 0.0174533f; // pitch
        MRAC_Control(&ctrl);
        
        // now check Phi
        int rbf_start = MRAC_N_STRUCT;
        for (int k = 0; k < MRAC_N_RBF; k++) {
            float z = x / x_scale;
            float c_k = 0.0f;
            float w = 1.0f;
            if (MRAC_N_RBF > 1) {
                float spacing = 2.0f / (MRAC_N_RBF - 1);
                c_k = -1.0f + k * spacing;
                w = 1.0f * spacing;
            } else {
                w = 2.0f;
            }
            float dz = z - c_k;
            float expected = expf(-dz * dz / (2.0f * w * w));
            float actual = mrac_state.pitch.Phi[rbf_start + k];
            
            if (fabsf(actual - expected) > 1e-6f + 1e-4f * expected) rbf_ok = 0;
        }
    }
    // check centre value = 1.0f
    for (int k = 0; k < MRAC_N_RBF; k++) {
        float c_k = 0.0f;
        if (MRAC_N_RBF > 1) {
            float spacing = 2.0f / (MRAC_N_RBF - 1);
            c_k = -1.0f + k * spacing;
        }
        CtrlerTypeDef ctrl;
        memset(&ctrl, 0, sizeof(ctrl));
        ctrl.gyroyPID.FB = (c_k * x_scale) / 0.0174533f;
        MRAC_Control(&ctrl);
        if (mrac_state.pitch.Phi[MRAC_N_STRUCT + k] != 1.0f) rbf_ok = 0;
    }
    if (rbf_ok) pass("rbf"); else fail("rbf");
#endif

    // c. library
#if MRAC_N_SINDY > 0
    int lib_ok = 1;
    CtrlerTypeDef ctrl;
    memset(&ctrl, 0, sizeof(ctrl));
    ctrl.gyroyPID.FB = 2.0f / 0.0174533f; // x = 2
    ctrl.gyroyPID.Des = 1.0f / 0.0174533f; // r = 1
    // Let's set some states directly and call MRAC_Control to get Phi
    ctrl.gyroyPID.U = 3.0f * mrac_config_pitch.mrac_to_mixer; // u_nom = 3
    // cross pitch: q_rate * r_rate = 2 * r_rate. let's set r_rate
    ctrl.gyrozPID.FB = 4.0f / 0.0174533f; // r_rate = 4. cross = 8
    
    // xm depends on reference model, let's step once
    MRAC_Control(&ctrl);
    
    float x = mrac_state.pitch.x;
    float abs_x = fabsf(x);
    float u = mrac_state.pitch.u_nom;
    float abs_u = fabsf(u);
    float cross = mrac_bus[MRAC_AXIS_PITCH].cross; // pitch cross = 2*4=8
    float xm = mrac_state.pitch.xm;
    
    int sindy_start = MRAC_N_STRUCT + MRAC_N_RBF;
    float expected[11];
#if MRAC_N_STRUCT == 0
    expected[0] = 1.0f;
    expected[1] = x;
    expected[2] = cross;
    expected[3] = u;
    expected[4] = xm;
    expected[5] = x * abs_x;
    expected[6] = x * x * x;
    expected[7] = x * u;
    expected[8] = x * cross;
    expected[9] = u * abs_u;
    expected[10] = xm * x;
#else
    expected[0] = x * abs_x;
    expected[1] = x * x * x;
    expected[2] = x * u;
    expected[3] = x * cross;
    expected[4] = u * abs_u;
    expected[5] = xm * x;
#endif

    for (int i = 0; i < MRAC_N_SINDY; i++) {
        float actual = mrac_state.pitch.Phi[sindy_start + i];
        // Bit-equal comparison
        uint32_t act_u, exp_u;
        memcpy(&act_u, &actual, 4);
        memcpy(&exp_u, &expected[i], 4);
        if (act_u != exp_u) lib_ok = 0;
    }
    if (lib_ok) pass("library"); else fail("library");
#endif

    // d. hybrid
#if MRAC_N_STRUCT > 0 && (MRAC_N_RBF > 0 || MRAC_N_SINDY > 0)
    int hybrid_ok = 1;
    CtrlerTypeDef ctrl_hyb;
    memset(&ctrl_hyb, 0, sizeof(ctrl_hyb));
    ctrl_hyb.gyroyPID.FB = 2.0f / 0.0174533f;
    ctrl_hyb.gyrozPID.FB = 4.0f / 0.0174533f;
    ctrl_hyb.gyroyPID.U = 3.0f * mrac_config_pitch.mrac_to_mixer;
    MRAC_Control(&ctrl_hyb);
    
    float x_h = mrac_state.pitch.x;
    float cross_h = mrac_bus[MRAC_AXIS_PITCH].cross;
    float u_h = mrac_state.pitch.u_nom;
    float xm_h = mrac_state.pitch.xm;
    
    float expected_struct[6] = {1.0f, x_h, x_h * tanhf(x_h), cross_h, u_h, xm_h};
    for (int i = 0; i < 6; i++) {
        uint32_t act_u, exp_u;
        float actual = mrac_state.pitch.Phi[i];
        memcpy(&act_u, &actual, 4);
        memcpy(&exp_u, &expected_struct[i], 4);
        if (act_u != exp_u) hybrid_ok = 0;
    }
    if (hybrid_ok) pass("hybrid"); else fail("hybrid");
#endif

    // e. closed loop: bounded, and the weights of the new block (all weights for the default variant) move
    int closed_loop_ok = run_closed_loop(0);
#if (MRAC_N_RBF + MRAC_N_SINDY) > 0
    if (count_nonzero_theta(MRAC_N_STRUCT, MRAC_N_FEATURES) == 0) closed_loop_ok = 0;
#else
    if (count_nonzero_theta(0, MRAC_N_FEATURES) == 0) closed_loop_ok = 0;
#endif
    if (closed_loop_ok) pass("closed_loop"); else fail("closed_loop");

    // f. frozen block: same drive with gamma = 0 for the whole new block, whose weights must stay exactly 0
#if (MRAC_N_RBF + MRAC_N_SINDY) > 0
    int frozen_ok = run_closed_loop(1);
    if (count_nonzero_theta(MRAC_N_STRUCT, MRAC_N_FEATURES) != 0) frozen_ok = 0;
    if (frozen_ok) pass("frozen_block"); else fail("frozen_block");
#endif

    return 0;
}
