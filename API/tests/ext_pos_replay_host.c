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
#include <string.h>
#include "ekf_of.h"

/* globals normally defined in StabilizerTask.c */
volatile uint8_t g_ekf_of_shadow = 1U;
volatile uint8_t g_ekf_of_vel_fb = 0U;

int main(void)
{
    static ext_pos_ring_t ring;
    static ext_pos_stat_t s;
    char line[160];
    float delay_ms = 0.0f, post_x = 0.0f, post_y = 0.0f;
    uint32_t post_t = 0U, seq = 0U, seen = 0U;
    ext_pos_ring_reset(&ring);
    memset(&s, 0, sizeof s);
    while (fgets(line, sizeof line, stdin)) {
        unsigned long now, idx, code;
        float ex, ey, x, y, dx, dy;
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
        if (sscanf(line, "T %lu %f %f", &now, &ex, &ey) == 3) {             /* Ext_Pos_Tick, mode 2 */
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
    printf("N %u %u %u %u %u\n", (unsigned)s.n_ok, (unsigned)s.n_rej, (unsigned)s.n_old, (unsigned)s.n_anchor,
           (unsigned)s.n_disagree);
    return 0;
}
