/* Host preview of an onboard preset program (CMD 0x1C): the firmware's own evaluator, API/wfb_prog.c.
 *
 * stdin:  hover_z_m dt_s, then WFB_PROG_F_COUNT floats per segment record (whitespace separated).
 * stdout: "commit <wfb_err_t> <err_seg|-1> <t_total_s>", then when the commit passed one
 *         "seg <k> <t0_s> <dur_s>" per segment and "pt <t_s> <x_m> <y_m> <z_m> <yaw_deg>" every dt_s
 *         until the program is DONE (the last pt is the end pose). Yaw is relative to the START heading.
 * Driven by ground_station/platform/wfb_program.py preview(); built with gcc like tools/host_tests.py. */
#include <stdio.h>
#include <string.h>
#include "wfb_prog.h"

static float s_rec[WFB_PROG_MAX_SEGS][WFB_PROG_F_COUNT];
static wfb_prog_seg_t s_buf[WFB_PROG_MAX_SEGS];

int main(void)
{
    wfb_prog_t pr;
    wfb_traj_limits_t lim;
    wfb_prog_caps_t caps;
    wfb_traj_point_t p;
    float hover_z, dt, v;
    uint32_t crc;
    wfb_err_t err = WFB_ERR_NONE;
    unsigned n = 0u, i = 0u, k;
    int run;
    long step;

    if (scanf("%f %f", &hover_z, &dt) != 2 || !(dt > 0.0f)) {
        fprintf(stderr, "header: hover_z_m dt_s\n");
        return 2;
    }
    while (scanf("%f", &v) == 1) {
        if (n >= WFB_PROG_MAX_SEGS) {
            fprintf(stderr, "more than %d segments\n", WFB_PROG_MAX_SEGS);
            return 2;
        }
        s_rec[n][i] = v;
        if (++i == WFB_PROG_F_COUNT) {
            i = 0u;
            n++;
        }
    }
    if (i != 0u || n == 0u) {
        fprintf(stderr, "need whole records of %d floats\n", WFB_PROG_F_COUNT);
        return 2;
    }

    wfb_traj_default_limits(&lim);
    wfb_prog_default_caps(&caps);
    wfb_prog_init(&pr, s_buf, WFB_PROG_MAX_SEGS);
    crc = wfb_crc32((const uint8_t *)s_rec, (uint32_t)((uint32_t)n * sizeof(s_rec[0])));
    err = wfb_prog_on_cmd(&pr, WFB_PROG_IDX_BEGIN, (float)n, &lim, &caps, hover_z);
    for (k = 0u; k < n && err == WFB_ERR_NONE; k++) {
        for (i = 0u; i < WFB_PROG_F_COUNT && err == WFB_ERR_NONE; i++) {
            err = wfb_prog_on_cmd(&pr, (uint8_t)i, s_rec[k][i], &lim, &caps, hover_z);
        }
        if (err == WFB_ERR_NONE) {
            err = wfb_prog_on_cmd(&pr, WFB_PROG_IDX_PUSH, (float)k, &lim, &caps, hover_z);
        }
    }
    if (err == WFB_ERR_NONE) {
        err = wfb_prog_on_cmd(&pr, WFB_PROG_IDX_CRC_HI, (float)(crc >> 16), &lim, &caps, hover_z);
    }
    if (err == WFB_ERR_NONE) {
        err = wfb_prog_on_cmd(&pr, WFB_PROG_IDX_COMMIT, (float)(crc & 0xFFFFu), &lim, &caps, hover_z);
    }
    printf("commit %d %d %.9g\n", (int)err, pr.err_seg == 0xFFFFu ? -1 : (int)pr.err_seg,
           err == WFB_ERR_NONE ? (double)pr.t_total : 0.0);
    if (err != WFB_ERR_NONE) {
        return 0;
    }
    for (k = 0u; k < n; k++) {
        printf("seg %u %.9g %.9g\n", k, (double)s_buf[k].t0, (double)s_buf[k].dur);
    }
    if (wfb_prog_start(&pr, 0.0f) != WFB_ERR_NONE) {
        fprintf(stderr, "start refused\n");
        return 3;
    }
    memset(&p, 0, sizeof(p));
    for (step = 0L, run = 1; run && step < 10000000L; step++) {
        float t = (float)step * dt;
        run = wfb_prog_sample(&pr, t, &p);
        printf("pt %.9g %.9g %.9g %.9g %.9g\n", (double)t, (double)p.x_m, (double)p.y_m, (double)p.z_m,
               (double)p.yaw_deg);
    }
    return 0;
}
