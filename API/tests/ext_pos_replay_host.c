/*
 * ext_pos_replay_host.c - Host driver for `live_tracker replay --fw` (ground_station/analysis/live_tracker.py): the
 *                         real API/ekf_of.c camera-fix path (ext_pos_decode, ring, fix, step, spread; float32, 5 ms
 *                         ticks, mode 2) on a replayed earth estimate. The task glue below mirrors Cmd_ExtPos
 *                         (TASK/send_data.c: t_meas = now - (idx * 10 + delay_ms), newest-wins post) and Ext_Pos_Tick
 *                         (TASK/StabilizerTask.c); keep the three in step.
 *
 * stdin, one record per line (cm, ms):
 *   D delay_ms               g_ext_pos_delay_ms
 *   F now_ms idx code        one CMD 0x2C fix frame drained at now_ms (idx = age in 10 ms units, code = wire value)
 *   T now_ms earth_x earth_y one control tick; earth = the raw path (earth_x/y minus the applied correction a)
 * stdout: "a_x a_y" (applied correction after the tick) per T, then "N n_ok n_rej n_old n_anchor n_disagree".
 *
 * Build: gcc -std=c99 -O2 -ffp-contract=off -IAPI -IAPI/tests/stubs_wp14 API/tests/ext_pos_replay_host.c API/ekf_of.c -lm
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include "ekf_of.h"

/* globals normally defined in StabilizerTask.c */
volatile uint8_t g_ekf_of_shadow = 1U;
volatile uint8_t g_ekf_of_vel_fb = 0U;

int main(int argc, char **argv)
{
    static ext_pos_ring_t ring;
    static ext_pos_stat_t s;
    static EkfOf_t ekf;
    char line[160];
    float delay_ms = 0.0f, post_x = 0.0f, post_y = 0.0f;
    uint32_t post_t = 0U, seq = 0U, seen = 0U, last_now = 0U;
    int mode = (argc > 1) ? atoi(argv[1]) : 2;
    ext_pos_ring_reset(&ring);
    memset(&s, 0, sizeof s);
    EkfOf_Init(&ekf);
    while (fgets(line, sizeof line, stdin)) {
        unsigned long now, idx, code;
        float ex, ey, x, y, dx, dy;
        int m;
        if (sscanf(line, "M %d", &m) == 1) {
            mode = m;
            continue;
        }
        if (sscanf(line, "D %f", &delay_ms) == 1) {
            continue;
        }
        if (sscanf(line, "F %lu %lu %lu", &now, &idx, &code) == 3) {        /* Cmd_ExtPos */
            if (ext_pos_decode((float)code, &x, &y)) {
                uint32_t back = (uint32_t)idx * 10U + (uint32_t)delay_ms;
                post_x = x;
                post_y = y;
                post_t = ((uint32_t)now > back) ? (uint32_t)now - back : 0U;
                seq++;
            }
            continue;
        }
        if (sscanf(line, "T %lu %f %f", &now, &ex, &ey) == 3) {             /* Ext_Pos_Tick */
            if (mode == 3) {
                s.c_x = s.a_x;
                s.c_y = s.a_y;
                ext_pos_ring_tick(&ring, (uint32_t)now, ex, ey);
                if (last_now != 0U && (uint32_t)now > last_now) {
                    float dt = (float)((uint32_t)now - last_now) * 0.001f;
                    EkfOf_Predict(&ekf, dt, 0.0f, 0.0f);
                }
                last_now = (uint32_t)now;
                if (seq != seen) {
                    seen = seq;
                    uint8_t res = ext_pos_fix(&s, &ring, post_t, (uint32_t)now, post_x, post_y);
                    if (ext_pos_take(&s, res)) {
                        const float g_of_scale = 1.094f;
                        float k = 100.0f * g_of_scale, r = EXT_POS_CAM_SD_CM / k;
                        float px0 = ekf.x[0], py0 = ekf.x[3];
                        float e_x = s.e_x, e_y = s.e_y;
                        float d = sqrtf(e_x * e_x + e_y * e_y);
                        if (d > EXT_POS_SLEW_CM) {
                            e_x *= EXT_POS_SLEW_CM / d;
                            e_y *= EXT_POS_SLEW_CM / d;
                        }
                        float hx = e_x / k;
                        float hy = e_y / k;
                        (void)EkfOf_UpdatePos(&ekf, px0 + hx, py0 + hy, r * r, EXT_POS_EKF_GATE);
                        s.a_x += (ekf.x[0] - px0) * k;
                        s.a_y += (ekf.x[3] - py0) * k;
                        s.c_x = s.a_x;
                        s.c_y = s.a_y;
                    }
                }
                ext_pos_stale_tick(&s, (uint32_t)now);
                printf("%.4f %.4f\n", s.a_x, s.a_y);
            } else {
                /* mode 2 */
                ext_pos_ring_tick(&ring, (uint32_t)now, ex, ey);
                if (seq != seen) {
                    seen = seq;
                    (void)ext_pos_step(&s, ext_pos_fix(&s, &ring, post_t, (uint32_t)now, post_x, post_y), &dx, &dy);
                }
                ext_pos_spread(&s, &dx, &dy);
                ext_pos_stale_tick(&s, (uint32_t)now);
                printf("%.4f %.4f\n", s.a_x, s.a_y);
            }
        }
    }
    printf("N %u %u %u %u %u\n", (unsigned)s.n_ok, (unsigned)s.n_rej, (unsigned)s.n_old, (unsigned)s.n_anchor,
           (unsigned)s.n_disagree);
    return 0;
}
