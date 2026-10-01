#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <math.h>

#include "mrac.h"

/* Features the dumps cover. The base tree has no MRAC_N_FEATURES (its storage is exactly the
   feature count); a wider MRAC_CAPACITY in the new tree only adds unused storage. */
#ifdef MRAC_N_FEATURES
    #define N_ACTIVE MRAC_N_FEATURES
#else
    #define N_ACTIVE MAX_NUM_BASIS
#endif

/* imu_data global definition for host test driver */
_imu_st imu_data = {0.0f, 0.0f};

/* 32-bit LCG deterministic pseudo-random generator */
static uint32_t g_lcg = 123456789U;

static inline float lcg_next_float(void) {
    g_lcg = g_lcg * 1664525U + 1013904223U;
    return ((float)(int32_t)g_lcg) / 2147483648.0f;
}

static inline float sum3sines(float t, float f1, float f2, float f3, float p1, float p2, float p3) {
    return (sinf(2.0f * 3.14159265f * f1 * t + p1) +
            sinf(2.0f * 3.14159265f * f2 * t + p2) +
            sinf(2.0f * 3.14159265f * f3 * t + p3)) / 3.0f;
}

static inline void print_float_hex(const char *tag, float val) {
    uint32_t u;
    memcpy(&u, &val, sizeof(u));
    printf("%s %08x\n", tag, u);
}

static inline void print_u32_hex(const char *tag, uint32_t u) {
    printf("%s %08x\n", tag, u);
}

static void print_axis_config(const char *name, const MRAC_AxisConfig_t *cfg) {
    char tag[128];
    for (int i = 0; i < N_ACTIVE; i++) {
        snprintf(tag, sizeof(tag), "cfg.%s.gamma.%d", name, i);
        print_float_hex(tag, cfg->gamma[i]);
    }
    snprintf(tag, sizeof(tag), "cfg.%s.sigma_lf", name);
    print_float_hex(tag, cfg->sigma_lf);
    snprintf(tag, sizeof(tag), "cfg.%s.sigma", name);
    print_float_hex(tag, cfg->sigma);
    snprintf(tag, sizeof(tag), "cfg.%s.gam_f", name);
    print_float_hex(tag, cfg->gam_f);
    snprintf(tag, sizeof(tag), "cfg.%s.omega_u", name);
    print_float_hex(tag, cfg->omega_u);
    for (int i = 0; i < N_ACTIVE; i++) {
        snprintf(tag, sizeof(tag), "cfg.%s.What_limit.%d", name, i);
        print_float_hex(tag, cfg->What_limit[i]);
    }
    for (int i = 0; i < N_ACTIVE; i++) {
        snprintf(tag, sizeof(tag), "cfg.%s.What_tol.%d", name, i);
        print_float_hex(tag, cfg->What_tol[i]);
    }
    for (int i = 0; i < N_ACTIVE; i++) {
        snprintf(tag, sizeof(tag), "cfg.%s.What_lower_limit.%d", name, i);
        print_float_hex(tag, cfg->What_lower_limit[i]);
    }
    snprintf(tag, sizeof(tag), "cfg.%s.lambda_perf", name);
    print_float_hex(tag, cfg->lambda_perf);
    snprintf(tag, sizeof(tag), "cfg.%s.tau_v", name);
    print_float_hex(tag, cfg->tau_v);
    snprintf(tag, sizeof(tag), "cfg.%s.u_max", name);
    print_float_hex(tag, cfg->u_max);
    snprintf(tag, sizeof(tag), "cfg.%s.mrac_to_mixer", name);
    print_float_hex(tag, cfg->mrac_to_mixer);
    snprintf(tag, sizeof(tag), "cfg.%s.J", name);
    print_float_hex(tag, cfg->J);
    snprintf(tag, sizeof(tag), "cfg.%s.e_deadzone", name);
    print_float_hex(tag, cfg->e_deadzone);
    snprintf(tag, sizeof(tag), "cfg.%s.e_freeze", name);
    print_float_hex(tag, cfg->e_freeze);
    snprintf(tag, sizeof(tag), "cfg.%s.e_sat", name);
    print_float_hex(tag, cfg->e_sat);
    snprintf(tag, sizeof(tag), "cfg.%s.k_e", name);
    print_float_hex(tag, cfg->k_e);
    snprintf(tag, sizeof(tag), "cfg.%s.ref_model_bw", name);
    print_float_hex(tag, cfg->ref_model_bw);
    snprintf(tag, sizeof(tag), "cfg.%s.ref_model_zeta", name);
    print_float_hex(tag, cfg->ref_model_zeta);
    snprintf(tag, sizeof(tag), "cfg.%s.P_lyap", name);
    print_float_hex(tag, cfg->P_lyap);
    snprintf(tag, sizeof(tag), "cfg.%s.ref_Q1", name);
    print_float_hex(tag, cfg->ref_Q1);
    snprintf(tag, sizeof(tag), "cfg.%s.ref_Q2", name);
    print_float_hex(tag, cfg->ref_Q2);
    snprintf(tag, sizeof(tag), "cfg.%s.wc_edot", name);
    print_float_hex(tag, cfg->wc_edot);
}

static void print_flags(const MRAC_FeatureFlags_t *f) {
    print_u32_hex("flag.adaptation_on", f->adaptation_on);
    print_u32_hex("flag.projection_on", f->projection_on);
    print_u32_hex("flag.deadzone_on", f->deadzone_on);
    print_u32_hex("flag.hard_freeze_on", f->hard_freeze_on);
    print_u32_hex("flag.tanh_saturation_on", f->tanh_saturation_on);
    print_u32_hex("flag.e_modification_on", f->e_modification_on);
    print_u32_hex("flag.l1_filtering_on", f->l1_filtering_on);
    print_u32_hex("flag.axis_enable_pitch", f->axis_enable_pitch);
    print_u32_hex("flag.axis_enable_roll", f->axis_enable_roll);
    print_u32_hex("flag.axis_enable_yaw", f->axis_enable_yaw);
    print_u32_hex("flag.output_injection_on", f->output_injection_on);
    print_u32_hex("flag.id_frame_on", f->id_frame_on);
    print_u32_hex("flag.of_frame_on", f->of_frame_on);
    print_u32_hex("flag.ref_model_type", f->ref_model_type);
}

static void print_simplex(const MRAC_Simplex_t *s) {
    print_u32_hex("simplex.mode", s->mode);
    print_u32_hex("simplex.variant", s->variant);
    print_u32_hex("simplex.tripped", s->tripped);
    print_u32_hex("simplex.reason", s->reason);
    print_u32_hex("simplex.trip_count", s->trip_count);
    print_u32_hex("simplex.would_trip_count", s->would_trip_count);
    print_u32_hex("simplex.sat_ticks.0", s->sat_ticks[0]);
    print_u32_hex("simplex.sat_ticks.1", s->sat_ticks[1]);
    print_u32_hex("simplex.sat_ticks.2", s->sat_ticks[2]);
    print_u32_hex("simplex.sat_ticks.3", s->sat_ticks[3]);
    print_u32_hex("simplex.clear_ticks", s->clear_ticks);
    print_u32_hex("simplex.hold_ticks", s->hold_ticks);
    print_u32_hex("simplex.sat_ticks_max", s->sat_ticks_max);
    print_float_hex("simplex.roll_max", s->roll_max);
    print_float_hex("simplex.pitch_max", s->pitch_max);
    print_float_hex("simplex.w_norm_max", s->w_norm_max);
    print_float_hex("simplex.fade", s->fade);
}

static void print_axis_step(const char *scn_name, int step, const char *axis_name, const MRAC_AxisState_t *st) {
    char tag[128];
    snprintf(tag, sizeof(tag), "%s:%d:%s:xm", scn_name, step, axis_name);
    print_float_hex(tag, st->xm);
    snprintf(tag, sizeof(tag), "%s:%d:%s:xm_dot", scn_name, step, axis_name);
    print_float_hex(tag, st->xm_dot);
    snprintf(tag, sizeof(tag), "%s:%d:%s:e", scn_name, step, axis_name);
    print_float_hex(tag, st->e);
    snprintf(tag, sizeof(tag), "%s:%d:%s:e_dot", scn_name, step, axis_name);
    print_float_hex(tag, st->e_dot);
    snprintf(tag, sizeof(tag), "%s:%d:%s:xdot_f", scn_name, step, axis_name);
    print_float_hex(tag, st->xdot_f);
    snprintf(tag, sizeof(tag), "%s:%d:%s:u_ad", scn_name, step, axis_name);
    print_float_hex(tag, st->u_ad);
    for (int i = 0; i < N_ACTIVE; i++) {
        snprintf(tag, sizeof(tag), "%s:%d:%s:Phi.%d", scn_name, step, axis_name, i);
        print_float_hex(tag, st->Phi[i]);
    }
    for (int i = 0; i < N_ACTIVE; i++) {
        snprintf(tag, sizeof(tag), "%s:%d:%s:Theta.%d", scn_name, step, axis_name, i);
        print_float_hex(tag, st->Theta[i]);
    }
    for (int i = 0; i < N_ACTIVE; i++) {
        snprintf(tag, sizeof(tag), "%s:%d:%s:Whatf.%d", scn_name, step, axis_name, i);
        print_float_hex(tag, st->Whatf[i]);
    }
}

static void init_simplex_defaults(void) {
    mrac_simplex.mode = 0;
    mrac_simplex.variant = 0;
    mrac_simplex.tripped = 0;
    mrac_simplex.reason = 0;
    mrac_simplex.trip_count = 0;
    mrac_simplex.would_trip_count = 0;
    mrac_simplex.sat_ticks[0] = 0;
    mrac_simplex.sat_ticks[1] = 0;
    mrac_simplex.sat_ticks[2] = 0;
    mrac_simplex.sat_ticks[3] = 0;
    mrac_simplex.clear_ticks = 0;
    mrac_simplex.hold_ticks = 200;
    mrac_simplex.sat_ticks_max = 40;
    mrac_simplex.roll_max = 3.14f;
    mrac_simplex.pitch_max = 3.14f;
    mrac_simplex.w_norm_max = 1.0e6f;
    mrac_simplex.fade = 1.0f;
}

int main(void) {
    static char stdout_buf[1048576];
    setvbuf(stdout, stdout_buf, _IOFBF, sizeof(stdout_buf));

    uint32_t cov_u_ad_sat = 0;
    uint32_t cov_e_freeze = 0;
    uint32_t cov_theta_upper_entry = 0;
    uint32_t cov_theta_lower_entry = 0;
    int prev_band[4][MAX_NUM_BASIS];
    uint32_t cov_simplex_trips = 0;
    uint8_t prev_tripped = 0;

    /* Initialize and print initial configs, flags, simplex */
    MRAC_Init();

    print_axis_config("pitch", &mrac_config_pitch);
    print_axis_config("roll", &mrac_config_roll);
    print_axis_config("yaw", &mrac_config_yaw);
    print_axis_config("z", &mrac_config_z);
    print_flags(&mrac_flags);
    print_simplex(&mrac_simplex);

    /* Scenario list:
     * 0: S0 init defaults
     * 1: S1 all flags on, l1_filtering_on=1, output_injection_on=1
     * 2: S2 projection_on=0
     * 3: S3 deadzone_on=0, tanh_saturation_on=0, e_modification_on=0
     * 4: S4 ref_model_type=1
     * 5: S5 ref_model_type=2
     * 6: S6 hard_freeze_on=0
     * 7: S7 mrac_simplex.mode=1 with w_norm_max=0.05f and sat_ticks_max=5
     * 8: S8 S1 plus MRAC_Reset() at step 2000
     * 9: S9 yaw and pitch disabled (axis_enable_*=0)
     * 10: SP (only if MRAC_ENABLE_SIGMA_PRIOR)
     */
#ifdef MRAC_ENABLE_SIGMA_PRIOR
    int num_scenarios = 11;
#else
    int num_scenarios = 10;
#endif

    for (int scn = 0; scn < num_scenarios; scn++) {
        char scn_name[16];
        if (scn < 10) {
            snprintf(scn_name, sizeof(scn_name), "S%d", scn);
        } else {
            snprintf(scn_name, sizeof(scn_name), "SP");
        }

        MRAC_Init();

        init_simplex_defaults();
        prev_tripped = 0;

#ifdef MRAC_ENABLE_SIGMA_PRIOR
        sigma_prior = 0.0f;
        memset(Theta_prior, 0, sizeof(Theta_prior));
#endif

        if (scn == 1 || scn == 8 || scn == 10) {
            mrac_flags.adaptation_on = 1;
            mrac_flags.projection_on = 1;
            mrac_flags.deadzone_on = 1;
            mrac_flags.hard_freeze_on = 1;
            mrac_flags.tanh_saturation_on = 1;
            mrac_flags.e_modification_on = 1;
            mrac_flags.l1_filtering_on = 1;
            mrac_flags.axis_enable_pitch = 1;
            mrac_flags.axis_enable_roll = 1;
            mrac_flags.axis_enable_yaw = 1;
            mrac_flags.output_injection_on = 1;
            mrac_flags.id_frame_on = 1;
            mrac_flags.of_frame_on = 1;
        }
        if (scn == 2) {
            mrac_flags.projection_on = 0;
        }
        if (scn == 3) {
            mrac_flags.deadzone_on = 0;
            mrac_flags.tanh_saturation_on = 0;
            mrac_flags.e_modification_on = 0;
        }
        if (scn == 4) {
            mrac_flags.ref_model_type = 1;
        }
        if (scn == 5) {
            mrac_flags.ref_model_type = 2;
        }
        if (scn == 6) {
            mrac_flags.hard_freeze_on = 0;
        }
        if (scn == 7) {
            mrac_simplex.mode = 1;
            mrac_simplex.w_norm_max = 0.05f;
            mrac_simplex.sat_ticks_max = 5;
        }
        if (scn == 9) {
            mrac_flags.axis_enable_pitch = 0;
            mrac_flags.axis_enable_yaw = 0;
        }
#ifdef MRAC_ENABLE_SIGMA_PRIOR
        if (scn == 10) {
            sigma_prior = 0.5f;
            for (int i = 0; i < N_ACTIVE; i++) {
                Theta_prior[0][i] = 0.4f * mrac_config_pitch.What_limit[i] * ((i % 2 == 0) ? 0.8f : 0.4f);
                Theta_prior[1][i] = 0.4f * mrac_config_roll.What_limit[i]  * ((i % 2 == 0) ? 0.8f : 0.4f);
                Theta_prior[2][i] = 0.4f * mrac_config_yaw.What_limit[i]   * ((i % 2 == 0) ? 0.8f : 0.4f);
                Theta_prior[3][i] = 0.4f * mrac_config_z.What_limit[i]     * ((i % 2 == 0) ? 0.8f : 0.4f);
            }
        }
#endif

        g_lcg = 123456789U + (uint32_t)scn * 10007U;
#ifdef MRAC_EQUIV_NEW_TREE
    mrac_in_armed = 1;
    mrac_in_phase = 1; // FLYING
    mrac_inj.learn_gate = 1;
    mrac_inj.inj_alpha = mrac_flags.output_injection_on ? 1.0f : 0.0f;
    mrac_inj.ramp_p = 1.0f;
    mrac_inj.fly_ticks = 200;
    mrac_inj.prev_injection_on = mrac_flags.output_injection_on;
#endif


        /* inside-band at start: a weight resting on a bound is not an entry */
        for (int a = 0; a < 4; a++)
            for (int i = 0; i < N_ACTIVE; i++) prev_band[a][i] = 3;
        for (int step = 0; step < 4000; step++) {
            if (scn == 8 && step == 2000) {
                MRAC_Reset();
            }

            float t = (float)step * MRAC_DT;
            CtrlerTypeDef ctrl;
            memset(&ctrl, 0, sizeof(ctrl));

            /* Command targets (Des) in deg/s for gyro PIDs (amplitude up to 300), m/s for Z (amplitude 1.5) */
            ctrl.gyroyPID.Des = 180.0f + 70.0f * sum3sines(t, 0.4f, 1.1f, 2.3f, 0.1f, 0.5f, 1.2f) + 10.0f * lcg_next_float();
            ctrl.gyroxPID.Des = 200.0f + 60.0f * sum3sines(t, 0.5f, 1.3f, 2.7f, 0.3f, 0.8f, 1.5f) + 10.0f * lcg_next_float();
            ctrl.gyrozPID.Des = 160.0f + 50.0f * sum3sines(t, 0.3f, 0.9f, 1.9f, 0.2f, 0.6f, 1.1f) + 10.0f * lcg_next_float();
            ctrl.Z_ratePID.Des = 1.0f  + 0.4f  * sum3sines(t, 0.3f, 0.8f, 1.7f, 0.5f, 1.1f, 1.9f) + 0.05f * lcg_next_float();

            /* Tracking feedback (FB): lags Des to create adaptation demand within active region */
            ctrl.gyroyPID.FB = ctrl.gyroyPID.Des - 25.0f + 10.0f * sum3sines(t, 0.7f, 1.7f, 3.1f, 0.5f, 1.0f, 2.0f) + 3.0f * lcg_next_float();
            ctrl.gyroxPID.FB = ctrl.gyroxPID.Des - 25.0f + 10.0f * sum3sines(t, 0.8f, 1.9f, 3.3f, 0.7f, 1.2f, 2.2f) + 3.0f * lcg_next_float();
            ctrl.gyrozPID.FB = ctrl.gyrozPID.Des - 20.0f + 8.0f  * sum3sines(t, 0.6f, 1.5f, 2.9f, 0.4f, 0.9f, 1.8f) + 3.0f * lcg_next_float();
            ctrl.Z_ratePID.FB = ctrl.Z_ratePID.Des - 0.2f + 0.1f * sum3sines(t, 0.5f, 1.2f, 2.5f, 0.2f, 0.7f, 1.5f) + 0.02f * lcg_next_float();

            /* 50-step burst to 1500 every 800 steps (steps 750..799, 1550..1599, etc.) */
            if ((step % 800) >= 750) {
                ctrl.gyroyPID.FB = 1500.0f;
                ctrl.gyroxPID.FB = 1500.0f;
                ctrl.gyrozPID.FB = 1500.0f;
                ctrl.Z_ratePID.FB = 5.0f;
                ctrl.gyroyPID.Des = 1400.0f;
                ctrl.gyroxPID.Des = 1400.0f;
                ctrl.gyrozPID.Des = 1400.0f;
                ctrl.Z_ratePID.Des = 3.5f;
            }

            /* Nominal control (U) in mixer units (up to 600 gyro, 400 Z) */
            ctrl.gyroyPID.U = 550.0f * sum3sines(t, 0.9f, 2.1f, 4.3f, 0.2f, 0.8f, 1.4f) + 30.0f * lcg_next_float();
            ctrl.gyroxPID.U = 550.0f * sum3sines(t, 1.0f, 2.3f, 4.5f, 0.4f, 1.0f, 1.6f) + 30.0f * lcg_next_float();
            ctrl.gyrozPID.U = 550.0f * sum3sines(t, 0.7f, 1.8f, 3.9f, 0.1f, 0.6f, 1.2f) + 30.0f * lcg_next_float();
            ctrl.Z_ratePID.U = 350.0f * sum3sines(t, 0.8f, 1.9f, 3.7f, 0.3f, 0.9f, 1.7f) + 20.0f * lcg_next_float();

            /* imu_data.pit/rol: small, above 3.2 rad for 20 steps in S7 only (steps 500..519) */
            if (scn == 7 && step >= 500 && step < 520) {
                imu_data.pit = 3.3f;
                imu_data.rol = 0.05f;
            } else {
                imu_data.pit = 0.05f * sum3sines(t, 0.2f, 0.7f, 1.3f, 0.1f, 0.4f, 0.9f);
                imu_data.rol = 0.05f * sum3sines(t, 0.3f, 0.8f, 1.5f, 0.2f, 0.5f, 1.1f);
            }

            MRAC_Control(&ctrl);

            /* Output per enabled axis */
            if (mrac_flags.axis_enable_pitch) {
                print_axis_step(scn_name, step, "pitch", &mrac_state.pitch);
            }
            if (mrac_flags.axis_enable_roll) {
                print_axis_step(scn_name, step, "roll", &mrac_state.roll);
            }
            if (mrac_flags.axis_enable_yaw) {
                print_axis_step(scn_name, step, "yaw", &mrac_state.yaw);
            }
            /* Z rate is always enabled */
            print_axis_step(scn_name, step, "z_rate", &mrac_state.z_rate);

            /* Simplex outputs */
            char smp_tag[128];
            snprintf(smp_tag, sizeof(smp_tag), "%s:%d:simplex:fade", scn_name, step);
            print_float_hex(smp_tag, mrac_simplex.fade);
            snprintf(smp_tag, sizeof(smp_tag), "%s:%d:simplex:tripped", scn_name, step);
            print_u32_hex(smp_tag, (uint32_t)mrac_simplex.tripped);

            /* Coverage tracking across all scenarios */
            int sat_this_tick = 0;
            if (fabsf(mrac_state.pitch.u_ad) >= 0.999f * mrac_config_pitch.u_max) sat_this_tick = 1;
            if (fabsf(mrac_state.roll.u_ad)  >= 0.999f * mrac_config_roll.u_max)  sat_this_tick = 1;
            if (fabsf(mrac_state.yaw.u_ad)   >= 0.999f * mrac_config_yaw.u_max)   sat_this_tick = 1;
            if (fabsf(mrac_state.z_rate.u_ad)>= 0.999f * mrac_config_z.u_max)     sat_this_tick = 1;
            if (sat_this_tick) cov_u_ad_sat++;

            int freeze_this_tick = 0;
            if (mrac_config_pitch.e_freeze > 0.0f && fabsf(mrac_state.pitch.e) > mrac_config_pitch.e_freeze) freeze_this_tick = 1;
            if (mrac_config_roll.e_freeze > 0.0f && fabsf(mrac_state.roll.e) > mrac_config_roll.e_freeze) freeze_this_tick = 1;
            if (mrac_config_yaw.e_freeze > 0.0f && fabsf(mrac_state.yaw.e) > mrac_config_yaw.e_freeze) freeze_this_tick = 1;
            if (mrac_config_z.e_freeze > 0.0f && fabsf(mrac_state.z_rate.e) > mrac_config_z.e_freeze) freeze_this_tick = 1;
            if (freeze_this_tick) cov_e_freeze++;

            /* Projection coverage: count ticks where the adaptive law drives a weight
               from outside into its upper / lower tolerance band (bit 1 / bit 2). */
            {
                const MRAC_AxisState_t *st[4] = { &mrac_state.pitch, &mrac_state.roll, &mrac_state.yaw, &mrac_state.z_rate };
                const MRAC_AxisConfig_t *cf[4] = { &mrac_config_pitch, &mrac_config_roll, &mrac_config_yaw, &mrac_config_z };
                int up_entry = 0, lo_entry = 0;
                for (int a = 0; a < 4; a++) {
                    for (int i = 0; i < N_ACTIVE; i++) {
                        int band = 0;
                        if (st[a]->Theta[i] >= cf[a]->What_limit[i] - cf[a]->What_tol[i]) band |= 1;
                        if (st[a]->Theta[i] <= cf[a]->What_lower_limit[i] + cf[a]->What_tol[i]) band |= 2;
                        if ((band & 1) && !(prev_band[a][i] & 1)) up_entry = 1;
                        if ((band & 2) && !(prev_band[a][i] & 2)) lo_entry = 1;
                        prev_band[a][i] = band;
                    }
                }
                if (up_entry) cov_theta_upper_entry++;
                if (lo_entry) cov_theta_lower_entry++;
            }

            if (!prev_tripped && mrac_simplex.tripped) {
                cov_simplex_trips++;
            }
            prev_tripped = mrac_simplex.tripped;
        }
    }

    /* Print coverage counters to stdout and stderr */
    printf("cov u_ad_sat %u\n", cov_u_ad_sat);
    printf("cov e_freeze %u\n", cov_e_freeze);
    printf("cov theta_upper_entry %u\n", cov_theta_upper_entry);
    printf("cov theta_lower_entry %u\n", cov_theta_lower_entry);
    printf("cov simplex_trips %u\n", cov_simplex_trips);

    fprintf(stderr, "cov u_ad_sat %u\n", cov_u_ad_sat);
    fprintf(stderr, "cov e_freeze %u\n", cov_e_freeze);
    fprintf(stderr, "cov theta_upper_entry %u\n", cov_theta_upper_entry);
    fprintf(stderr, "cov theta_lower_entry %u\n", cov_theta_lower_entry);
    fprintf(stderr, "cov simplex_trips %u\n", cov_simplex_trips);

    return 0;
}
