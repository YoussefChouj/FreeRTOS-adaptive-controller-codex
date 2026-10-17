#include <stdio.h>
#include <stdint.h>
#include <math.h>
#include <string.h>
#include <assert.h>

#include "mrac.h"
#include "mrac_math.h"
#include "xy_ctrl.h"
#include "exp_row.h"

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

_imu_st imu_data;
static CtrlerTypeDef ctrl;

static const XyRm_t s_xyrm[4] = {
    { 1.0f, 2.0f,    0.0f, 1.0f }, /* 0: PID */
    { 1.0f, 2.2007f, 0.0f, 1.0f }, /* 1: ADRC */
    { 1.0f, 4.7842f, 0.0f, 1.0f }, /* 2: LQR */
    { 1.0f, 4.3675f, 0.0f, 1.0f }, /* 3: MPC */
};

#define N_PTS 2000
#define DT 0.01f

static float s_pos_des[N_PTS];
static float s_vff[N_PTS];

static void gen_figure8(void)
{
    float a_cm = 70.0f;
    float move_s = 20.0f;
    float ramp_s = 2.0f;
    float laps = 2.0f;
    float path_s = move_s - ramp_s;
    float om = 2.0f * (float)M_PI * laps / path_s;

    for (int i = 0; i < N_PTS; i++) {
        float ti = (float)i * DT;
        float warp;
        if (ti <= 0.0f) warp = 0.0f;
        else if (ti >= move_s) warp = path_s;
        else if (ti < ramp_s) warp = 0.5f * ti - ramp_s / (2.0f * (float)M_PI) * sinf((float)M_PI * ti / ramp_s);
        else if (ti > move_s - ramp_s) {
            float ui = move_s - ti;
            warp = path_s - (0.5f * ui - ramp_s / (2.0f * (float)M_PI) * sinf((float)M_PI * ui / ramp_s));
        } else {
            warp = 0.5f * ramp_s + (ti - ramp_s);
        }
        float th = om * warp;
        s_pos_des[i] = 0.5f * a_cm * sinf(2.0f * th);
    }

    for (int i = 0; i < N_PTS; i++) {
        if (i == 0) s_vff[i] = (s_pos_des[1] - s_pos_des[0]) / DT;
        else if (i == N_PTS - 1) s_vff[i] = (s_pos_des[N_PTS - 1] - s_pos_des[N_PTS - 2]) / DT;
        else s_vff[i] = (s_pos_des[i + 1] - s_pos_des[i - 1]) / (2.0f * DT);
    }
}

typedef struct {
    float kp, ki, kd, umax, upmax, uimax, udmax, summax, emin;
    float sum, pre;
} SimPid_t;

static void pid_init(SimPid_t *p, float kp, float ki, float kd, float umax, float upmax, float uimax, float udmax, float summax, float emin)
{
    p->kp = kp; p->ki = ki; p->kd = kd;
    p->umax = umax; p->upmax = upmax; p->uimax = uimax; p->udmax = udmax;
    p->summax = summax; p->emin = emin;
    p->sum = 0.0f; p->pre = 0.0f;
}

static float pid_step(SimPid_t *p, float e)
{
    if (fabsf(e) < p->emin) {
        p->sum += e;
        if (p->sum > p->summax) p->sum = p->summax;
        if (p->sum < -p->summax) p->sum = -p->summax;
    }
    float ui = p->ki * p->sum;
    if (ui > p->uimax) ui = p->uimax;
    if (ui < -p->uimax) ui = -p->uimax;

    float up = p->kp * e;
    if (up > p->upmax) up = p->upmax;
    if (up < -p->upmax) up = -p->upmax;

    float ud = p->kd * (e - p->pre);
    if (ud > p->udmax) ud = p->udmax;
    if (ud < -p->udmax) ud = -p->udmax;

    p->pre = e;
    float u = up + ui + ud;
    if (u > p->umax) u = p->umax;
    if (u < -p->umax) u = -p->umax;
    return u;
}

static void init_mrac(int kind, int injection_on)
{
    MRAC_Init();
    memset(&mrac_state, 0, sizeof(mrac_state));
    memset(&mrac_flags, 0, sizeof(mrac_flags));
    mrac_flags.adaptation_on = 1;
    mrac_flags.axis_enable_x = 1;
    mrac_flags.output_injection_on = injection_on ? 1 : 0;
    mrac_in_armed = 1;
    mrac_in_phase = MRAC_PHASE_FLYING;
    mrac_inj.fly_ticks = 65535;
    mrac_inj.prev_armed = 1;
    mrac_inj.prev_injection_on = injection_on ? 1 : 0;
    mrac_inj.ramp_p = 1.0f;
    mrac_inj.inj_alpha = 1.0f;
    mrac_inj.learn_gate = 1;
    mrac_simplex.mode = 0;
    mrac_simplex.tripped = 0;
    mrac_simplex.fade = 1.0f;

    MRAC_VariantParamSet(MRAC_AXIS_X, MRAC_VF_REF_TYPE,     s_xyrm[kind].ref_type);
    MRAC_VariantParamSet(MRAC_AXIS_X, MRAC_VF_REF_MODEL_BW, s_xyrm[kind].bw);
    MRAC_VariantParamSet(MRAC_AXIS_X, MRAC_VF_CRM_ELL,      s_xyrm[kind].crm_ell);
    MRAC_VariantParamSet(MRAC_AXIS_X, MRAC_VF_GAMMA_SCALE,  s_xyrm[kind].g);
}

static void sim_run(int kind, float dist_acc, int injection_on,
                    float *out_rms_uad, float *out_rms_unom, float *out_rms_pos_err)
{
    init_mrac(kind, injection_on);

    float k = 0.674429432f;
    float tau = 0.0674f;
    float drag = 0.42558222f;
    float delay = 12;
    int lmax = 13;
    float buf[13] = {0};
    float al = expf(-DT / tau);
    float a = 0.0f, v = 0.0f, pos = 0.0f;
    float amax = 262.858158f;

    SimPid_t pos_pid, vel_pid;
    XyCtrlState xyc;
    if (kind == 0) {
        pid_init(&pos_pid, 0.8f, 0.0013f, 4.0f, 300.0f, 300.0f, 5.0f, 50.0f, 3850.0f, 10.0f);
        pid_init(&vel_pid, 3.0f, 0.008f, 6.0f, 600.0f, 600.0f, 100.0f, 100.0f, 12500.0f, 10.0f);
    } else {
        XyCtrl_Reset(&xyc);
        XyCtrl_SetDelay(12);
        pid_init(&pos_pid, 0.8f, 0.0013f, 4.0f, 300.0f, 300.0f, 5.0f, 50.0f, 3850.0f, 10.0f);
    }

    double sum_uad_sq = 0.0;
    double sum_unom_sq = 0.0;
    double sum_pos_err_sq = 0.0;
    int count = 0;

    for (int i = 0; i < N_PTS; i++) {
        float pos_d = s_pos_des[i];
        float vff_d = s_vff[i];
        float vel_fb = v * 100.0f;

        float vd = pid_step(&pos_pid, pos_d - pos);
        if (vd > 120.0f) vd = 120.0f;
        if (vd < -120.0f) vd = -120.0f;
        vd += vff_d;

        float acmd = 0.0f;
        if (kind == 0) {
            acmd = pid_step(&vel_pid, vd - vel_fb);
        } else {
            acmd = XyCtrl_Step(&xyc, (uint8_t)kind, pos_d, pos, vel_fb, vff_d);
        }

        /* Feed MRAC */
        ctrl.locxsPID.FB = vel_fb;
        ctrl.locxsPID.Des = vd;
        mrac_xy_unom[0] = acmd;
        MRAC_Control(&ctrl);

        float u_ad_cm = mrac_state.x_vel.u_ad * 100.0f; /* m/s^2 -> cm/s^2 */
        float acmd_total = acmd;
        if (injection_on) {
            acmd_total += u_ad_cm;
        }

        if (kind != 0) {
            XyCtrl_Commit(&xyc, acmd_total);
        }

        /* Plant update */
        float acc = acmd_total;
        if (acc > amax) acc = amax;
        if (acc < -amax) acc = -amax;
        buf[i % lmax] = acc / 100.0f;

        int delayed_idx = (i - (int)delay + lmax) % lmax;
        float u_delayed = buf[delayed_idx];
        a = al * a + (1.0f - al) * u_delayed;
        v = v + DT * (k * a - drag * v + (dist_acc / 100.0f));
        pos = pos + v * 100.0f * DT;

        /* Skip initial transient (first 2 s = 200 ticks) for evaluation */
        if (i >= 200) {
            float unom_si = acmd / 100.0f;
            float uad_si = mrac_state.x_vel.u_ad;
            sum_uad_sq += (double)(uad_si * uad_si);
            sum_unom_sq += (double)(unom_si * unom_si);

            float pe = pos_d - pos;
            sum_pos_err_sq += (double)(pe * pe);
            count++;
        }
    }

    if (count > 0) {
        *out_rms_uad = (float)sqrt(sum_uad_sq / count);
        *out_rms_unom = (float)sqrt(sum_unom_sq / count);
        *out_rms_pos_err = (float)sqrt(sum_pos_err_sq / count);
    }
}

int main(void)
{
    gen_figure8();

    /* Constant force disturbance = 50.0 cm/s^2 (PROPOSED) */
    const float dist_force_cms2 = 50.0f;

    for (int kind = 0; kind < 4; kind++) {
        float rms_uad = 0.0f, rms_unom = 0.0f, pos_err_clean = 0.0f;
        /* 1. No disturbance: RMS(u_ad) < 0.05 * RMS(u_nom) */
        sim_run(kind, 0.0f, 1, &rms_uad, &rms_unom, &pos_err_clean);

        printf("kind %d no-dist: rms(u_ad)=%f, rms(u_nom)=%f, ratio=%f\n",
               kind, rms_uad, rms_unom, (rms_unom > 1e-4f) ? (rms_uad / rms_unom) : 0.0f);
        assert(rms_uad < 0.05f * rms_unom);

        /* 2. Constant force disturbance: tracking-error RMS with injection < without */
        float uad_dist = 0.0f, unom_dist = 0.0f;
        float err_no_inj = 0.0f, err_with_inj = 0.0f;
        sim_run(kind, dist_force_cms2, 0, &uad_dist, &unom_dist, &err_no_inj);
        sim_run(kind, dist_force_cms2, 1, &uad_dist, &unom_dist, &err_with_inj);

        printf("kind %d dist: err_no_inj=%f, err_with_inj=%f\n",
               kind, err_no_inj, err_with_inj);
        assert(err_with_inj < err_no_inj);
    }

    printf("mrac_xy_nofight: all 4 kinds passed no-fight and disturbance rejection\n");
    return 0;
}
