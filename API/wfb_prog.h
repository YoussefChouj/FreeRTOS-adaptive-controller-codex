#ifndef WFB_PROG_H
#define WFB_PROG_H

/* Onboard preset program (docs/workflow-c/onboard-preset-program.md): a list of segments, each one geometry
 * atom (LINE, TURN, ARC, LISSA, HOLD) moved along by one velocity profile (TRAP, SCURVE, SINE, QUINTIC).
 * Uploaded as parameters over CMD 0x1C (sticky staging record), validated analytically at COMMIT, evaluated
 * every tick. Pure C, builds on the host. Yaw is relative to the heading held at START. */

#include <stdint.h>
#include "wfb_types.h"
#include "wfb_traj.h"

#define WFB_PROG_MAX_SEGS 64u

typedef enum {
    WFB_ATOM_HOLD = 0,   /* P0 dwell_s */
    WFB_ATOM_LINE,       /* P0 x, P1 y, P2 z (m, absolute), P3 yaw_deg (relative); v m/s */
    WFB_ATOM_TURN,       /* P0 yaw_deg (relative); v deg/s, a deg/s^2, j deg/s^3 */
    WFB_ATOM_ARC,        /* P0 cx, P1 cy (m), P2 sweep_deg (+ = CCW), P3 dz (m), P4 yaw_mode (0 hold, 1 turn with arc) */
    WFB_ATOM_LISSA,      /* P0-2 amp x/y/z (m), P3-5 cycles-per-cycle n x/y/z, P6-8 phase x/y/z (deg), P9 cycles;
                          * v cycles/s, a cycles/s^2, j cycles/s^3 */
    WFB_ATOM_COUNT
} wfb_atom_t;

typedef enum {
    WFB_PROF_TRAP = 0,   /* constant-accel ramps */
    WFB_PROF_SCURVE,     /* 7-segment jerk-limited ramps (uses j) */
    WFB_PROF_SINE,       /* (1 - cos) ramps */
    WFB_PROF_QUINTIC,    /* min-jerk ramps, zero accel at both ends */
    WFB_PROF_COUNT
} wfb_prof_t;

/* Staged fields = CMD 0x1C idx 0..18 (sticky: keep their value until the next BEGIN). */
enum {
    WFB_PROG_F_ATOM = 0,
    WFB_PROG_F_PROFILE,
    WFB_PROG_F_V,        /* cruise speed (units by atom) */
    WFB_PROG_F_A,        /* ramp acceleration */
    WFB_PROG_F_J,        /* ramp jerk (SCURVE) */
    WFB_PROG_F_V_IN,     /* speed entering the segment (<= v) */
    WFB_PROG_F_V_OUT,    /* speed leaving the segment (<= v) */
    WFB_PROG_F_P0,       /* atom parameters P0..P11 */
    WFB_PROG_F_COUNT = WFB_PROG_F_P0 + 12
};

/* Control slots of CMD 0x1C. */
#define WFB_PROG_IDX_BEGIN  32u  /* val = segment count; resets the staging record to zero */
#define WFB_PROG_IDX_PUSH   33u  /* val = index of the segment being pushed (sequence check) */
#define WFB_PROG_IDX_CRC_HI 34u
#define WFB_PROG_IDX_COMMIT 35u  /* val = crc low 16 bits */
#define WFB_PROG_IDX_CLEAR  36u

typedef struct {
    float f[WFB_PROG_F_COUNT];  /* raw fields as uploaded (the CRC covers these) */
    /* derived at COMMIT */
    float t0, dur;              /* start time in the program, duration (s) */
    float len, vc;              /* path length (atom units), planned cruise speed */
    float ta0, tc, ta1;         /* ramp-in, cruise, ramp-out times */
    float rho0, rho1;           /* SCURVE jerk fraction of each ramp */
    float d0;                   /* ramp-in distance */
    float x0, y0, z0, yaw0;     /* start pose (yaw relative) */
    float dyaw;                 /* LINE/TURN: signed yaw change */
    float r, th0;               /* ARC: radius, start angle (rad) */
    float pad;
} wfb_prog_seg_t;

typedef struct {
    float a_max;                /* m/s^2 ramp and peak accel (LINE, ARC, LISSA) */
    float j_max;                /* m/s^3 */
    float yaw_rate_max;         /* deg/s (TURN, LINE yaw, ARC follow) */
    float yaw_acc_max;          /* deg/s^2 */
    float r_min;                /* m, ARC */
    float t_max;                /* s, whole program */
    float v_jump_max;           /* m/s, velocity step allowed where two segments meet (and at both program ends) */
} wfb_prog_caps_t;

typedef struct {
    wfb_prog_seg_t *seg;
    uint16_t cap, n, rx, cur;
    uint16_t err_seg;           /* segment that failed the last COMMIT, 0xFFFF = none */
    uint16_t crc_hi;
    uint8_t  crc_hi_set;
    uint8_t  state;             /* wfb_traj_state_t */
    uint32_t crc_calc;
    float    stage[WFB_PROG_F_COUNT];
    float    t_total;
    float    yaw_ref;           /* heading held at START, added to every yaw */
} wfb_prog_t;

void      wfb_prog_init(wfb_prog_t *pr, wfb_prog_seg_t *buf, uint16_t cap);
void      wfb_prog_default_caps(wfb_prog_caps_t *out);
wfb_err_t wfb_prog_on_cmd(wfb_prog_t *pr, uint8_t idx, float val,
                          const wfb_traj_limits_t *lim, const wfb_prog_caps_t *caps, float hover_z_m);
wfb_err_t wfb_prog_start(wfb_prog_t *pr, float yaw_ref_deg);
wfb_err_t wfb_prog_stop(wfb_prog_t *pr);
wfb_err_t wfb_prog_clear(wfb_prog_t *pr);
int       wfb_prog_sample(wfb_prog_t *pr, float t_s, wfb_traj_point_t *out); /* 1 running, 0 finished (DONE) */
float     wfb_prog_ramp_F(uint8_t prof, float rho, float tau); /* unit ramp integral, exposed for tests */

#endif /* WFB_PROG_H */
