/**
 * @file     ekf_of.h
 * @brief    8-state body-frame OF position + velocity-bias + accel-bias Kalman Filter.
 *
 * State vector: [pos_x, vel_x, bof_x, pos_y, vel_y, bof_y, ba_x, ba_y] — 8 states.
 * All matrices stored as flat row-major float arrays.
 *
 * Model:
 *   - Constant-velocity motion model with accelerometer input
 *   - OF bias (bof) and accelerometer bias (ba) are random walks
 *   - Measurements:
 *       - OF: debiased OF body-frame velocity (m/s) per axis
 *       - ZUPT: zero velocity update on the ground
 *
 * Units:
 *   - pos: m
 *   - vel: m/s
 *   - bof: m/s
 *   - ba: m/s^2
 *
 * Pure C99, no malloc. Compatible with Keil ARMCC.
 */
#ifndef __EKF_OF_H__
#define __EKF_OF_H__

#include <stdint.h>

/** 8-state OF position+bias EKF. */
typedef struct {
    /* state — 8 floats: [pos_x, vel_x, bof_x, pos_y, vel_y, bof_y, ba_x, ba_y] */
    float x[8];
    /* covariance — two 4x4 row-major blocks: P[0] for X axis, P[1] for Y axis */
    float P[2][16];
    /* Q diagonals */
    float q_pos;
    float q_acc;
    float q_bof;
    float q_ba;
    /* R */
    float R_of;
    float R_of1;        /* of1 raw-flow channel (EkfOf_UpdateRaw); <= 0 = channel off */
    float R_zupt;
    /* Innovation */
    float innov_x;
    float innov_y;
    uint8_t inited;
    /* OF innovation gate (PX4 EKF2_OF_GATE style): skip a sample when innov^2 > of_gate^2 * S */
    float of_gate;      /* sigmas; 0 = gate off */
    float of1_gate;     /* same, for the of1 channel; no gate-lockout release (of2's release re-opens P_vv) */
    uint32_t rej_x;     /* rejected OF samples since init */
    uint32_t rej_y;
    uint8_t rej_run_x;  /* consecutive rejected OF samples, see EKF_OF_REJ_RELEASE */
    uint8_t rej_run_y;
    uint8_t pos_rej_run_x;  /* consecutive rejected camera fixes (EkfOf_UpdatePos), same release rule */
    uint8_t pos_rej_run_y;
} EkfOf_t;

/* Gate-lockout recovery: after this many consecutive rejected OF samples on one axis
 * (0.1 s at the 50 Hz OF frame rate) that axis's velocity variance is reset to its P0
 * and the same sample is applied, so the KF re-acquires the measured velocity.
 * WHY (2026-10-03): all five logged flights (roaming_and_landing_1..4, x_y_calibration_
 * hitting_wall_1) tripped the health gate this way. ZUPT on the ground (or a hover)
 * shrinks P_vv until the 5-sigma gate is ~6 cm/s; a lift-off slide (+10..12 cm/s) or a
 * hard stop (0.5 -> -0.7 m/s in 0.5 s) leaves the gate, every sample is rejected, the
 * tilt-only predict cannot follow, and 0.5 s later the trip drops mode 2 to FIXED for
 * the rest of the flight. A single glitch frame is still rejected. */
#define EKF_OF_REJ_RELEASE      5U
#define EKF_OF_POS_P0           1.0f    /* position variance at init and at a camera-fix release, m^2 */

/** Call once at boot or after Reset_World_Origin. */
extern void EkfOf_Init(EkfOf_t *e);

/** Predict step.
 *  dt: step size, seconds.
 *  ax, ay: body accel in OF frame, m/s^2. */
extern void EkfOf_Predict(EkfOf_t *e, float dt, float ax, float ay);

/** Optical-flow velocity measurement update (body XY).
 *  of_x, of_y: raw OF body-frame velocity, m/s.
 *  Subtracts the OF bias estimate internally. */
extern void EkfOf_Update(EkfOf_t *e, float of_x, float of_y);

/** Second velocity measurement: the module's raw flow (of1, no gyro fix), m/s, same axes as
 *  EkfOf_Update. h = [0, 1, 0, 0] (no OF bias), gated at of1_gate. Call right after EkfOf_Update.
 *  WHY (2026-10-07): on video truth the gyro-fixed of2 carries a slow bias that bof cannot learn
 *  without a second, bias-free reference; of1 gives that reference (replay ekf_of_replay truth). */
extern void EkfOf_UpdateRaw(EkfOf_t *e, float of1_x, float of1_y);

/** Camera position fix (mode 3), h = [1, 0, 0, 0] per axis. z_x, z_y: absolute heading-frame position, m
 *  (x_now + the delayed innovation, see Ext_Pos_Tick). R: variance, m^2. gate: sigmas, 0 = off. After
 *  EKF_OF_REJ_RELEASE gated fixes in a row on one axis its position variance goes back to EKF_OF_POS_P0
 *  and the fix is applied (re-anchor). Returns the gated axes: bit0 x, bit1 y.
 *  Twin: ground_station/analysis/ekf_of_model.py EkfOfModel.update_pos. */
extern uint8_t EkfOf_UpdatePos(EkfOf_t *e, float z_x, float z_y, float R, float gate);

/** Zero velocity update (on ground). */
extern void EkfOf_UpdateZeroVel(EkfOf_t *e);

/** Reset position states to zero (call on Reset_World_Origin).
 *  Keeps vel and bias estimates intact. */
extern void EkfOf_ResetPos(EkfOf_t *e);

/** Zero both OF-bias states (bof_x, bof_y) and set their variance to var
 *  with no cross-covariance. Called on ARM and on the handheld-test edge. */
extern void EkfOf_ResetBias(EkfOf_t *e, float var);

/* bof variance at ARM, (m/s)^2. Since 2026-10-07 bof starts at 0 and is learned through q_bof
 * against the of1 channel (2026-10-03: was 2.5e-5; the lift-off slide was still learned as bias). */
#define EKF_OF_BOF_ARM_VAR      0.0f

/* ----- WP-14: shadow mode, health gate, active path, tilt gain ----- */

/* Shadow: 1 = KF runs every tick regardless of g_of_bias_mode, but does
 * NOT feed the control path.  Default 1 (shadow on).
 * WHY: every flight produces KF telemetry for offline validation without
 * touching the control path.  Set to 0 only via livewatch poke. */
extern volatile uint8_t g_ekf_of_shadow;

/* Active velocity feedback: 1 = locxsPID/locysPID.FB take KF velocity
 * instead of raw OF.  Default 0 (off until a shadow flight validates).
 * WHY: the ~55 ms lead of KF velocity over OF can tighten the vel loop,
 * but must not be enabled until a shadow flight matches the replay. */
extern volatile uint8_t g_ekf_of_vel_fb;

/* Tilt gain: the gravity-tilt term is scaled by this before entering the
 * KF predict.  Median of the per-axis fits on the five pinned logs
 * (ekf_of_replay TILT_FIT, 0.2 s windows, flight only):
 *   shadow3 0.200/0.180  shadow4 0.307/0.223  shadow5 0.198/0.212
 *   active5 0.260/0.317  active6 0.384/0.364   (k_x/k_y, all > 0)
 * -> median 0.242.  Windows 0.1 s / 0.5 s give median 0.251 / 0.172, all k > 0.
 * WHY: the OF velocity follows only ~1/4 of g*tilt (drag, OF scale, attitude
 * loop), so unit gain would over-drive v; the fitted gain keeps the KF
 * velocity in step with OF.  The signs EKF_OF_ACC_SIGN_X/Y in
 * StabilizerTask.c are the ones that make every k positive. */
#define EKF_OF_TILT_GAIN  0.242f

/* Health gate — innovation-based persistence counter.
 * Trip after EKF_OF_HEALTH_PERSIST consecutive ticks (at 200 Hz = 5 ms)
 * with innovation magnitude > EKF_OF_HEALTH_THRESH.
 * WHY: 2 m/s (WP-8) is ~50x the innovation sd; this is ~5x, and the
 * 100-tick persistence avoids single-sample false trips. */
#define EKF_OF_HEALTH_THRESH    0.061f  /* m/s — 5 x median per-axis innovation sd 1.22 cm/s (5 pinned logs, replay CHOSEN) */
#define EKF_OF_HEALTH_PERSIST   100U    /* ticks — 0.5 s at 200 Hz */

/* ---- External position fix (camera): shadow (mode 1), offset outside the EKF (mode 2), in-EKF fix (mode 3) ----
 * Design: docs/video-truth/live_camera_fusion_design_2026-10-11.md. StabilizerTask keeps the raw earth path
 * (earth_x/y minus the running correction c, cm) every EXT_POS_RING_DT_MS; Cmd_ExtPos (TASK/send_data.c, CMD 0x2C)
 * posts a fix with its capture time; ext_pos_fix() takes e = z - (path(t_meas) + c). Shadow: telemetry only.
 * Closed loop: ext_pos_step() moves the target c by EXT_POS_GAIN * e, at most EXT_POS_SLEW_CM per fix, and
 * ext_pos_spread() moves the applied part a (in earth_x/y) toward c by at most EXT_POS_SLEW_CM / EXT_POS_SPREAD_N per
 * control tick (no one-tick step into the locx/locyPID D term, which is on the error); after
 * EXT_POS_REJ_N gated fixes in a row the gated ones are taken too, through the same clamp (slewed re-anchor), until
 * one is back inside the gate, and only when their offsets z - path agree within EXT_POS_AGREE_CM (ext_pos_take()).
 * No fix for EXT_POS_STALE_MS: c holds (OF-only fallback), and the camera counts again after EXT_POS_TRUST_N accepted
 * fixes in a row. Mode 3 (PROPOSED): c follows a, so e = z - earth(t_meas); each fix ext_pos_take() passes goes to
 * EkfOf_UpdatePos() as x_now + e (heading frame, metres, sd EXT_POS_CAM_SD_CM, gate EXT_POS_EKF_GATE). Replayed rule:
 * ground_station/analysis/video_truth.py ext_pos_replay(), same constants. */
#define EXT_POS_RING_N        32U      /* 32 x 50 ms = 1.55 s of earth path */
#define EXT_POS_RING_DT_MS    50U
#define EXT_POS_GATE_CM       40.0f    /* design choice, PROPOSED; R3 replay had no camera outliers */
#define EXT_POS_STALE_MS      3000U
#define EXT_POS_Q_CM          0.5f     /* wire quantum: 12-bit signed per axis, +-1023.5 cm */
#define EXT_POS_GAIN          0.9f     /* per fix; R4 live replay 2026-10-12 (0.1: moves 14.9-15.5 cm at 0.15 s), PROPOSED */
#define EXT_POS_SLEW_CM       10.0f    /* max |dc| per fix; 3 cm was the moving-flight floor in replay, PROPOSED */
#define EXT_POS_SPREAD_N      20U      /* control ticks per full slew step: 0.5 cm per 5 ms tick (1 m/s), PROPOSED */
#define EXT_POS_REJ_N         3U       /* gated fixes in a row before the slewed re-anchor */
#define EXT_POS_AGREE_CM      5.0f     /* re-anchor only when the last EXT_POS_REJ_N offsets lie within this of their mean, PROPOSED */
#define EXT_POS_TRUST_N       3U       /* accepted fixes in a row before a stale camera counts again, PROPOSED */
#define EXT_POS_HIST_N        3U       /* offsets kept for the agreement rule, >= EXT_POS_REJ_N */
#define EXT_POS_CAM_SD_CM     1.7f     /* mode 3 R: camera sd, r5_live_2 (measured 2026-10-12) */
#define EXT_POS_EKF_GATE      5.0f     /* mode 3 EkfOf_UpdatePos gate, sigmas, PROPOSED (twin sim) */
typedef struct {
    float    x[EXT_POS_RING_N];
    float    y[EXT_POS_RING_N];
    uint32_t t[EXT_POS_RING_N];        /* sample time, ms */
    uint8_t  head;                     /* newest sample */
    uint8_t  count;
} ext_pos_ring_t;
typedef struct {
    float    e_x, e_y;                 /* last innovation z - earth(t_meas), cm (also kept when gated) */
    uint16_t n_ok, n_rej, n_old;       /* accepted, gated (|e| > gate), older than the buffer */
    uint16_t age_ms;                   /* now - t_meas of the last fix */
    uint8_t  rej_run;                  /* consecutive gated fixes (re-anchor rule, applied in mode 2) */
    uint16_t n_anchor;                 /* gated fixes taken by the re-anchor rule (mode 2) */
    float    c_x, c_y;                 /* running correction added to earth_x/y, cm (mode 2 moves it, else held) */
    uint8_t  stale;                    /* 1 = no accepted fix for EXT_POS_STALE_MS */
    uint32_t last_ok_ms;
    float    a_x, a_y;                 /* applied part of c, already in earth_x/y (ext_pos_spread), cm */
    uint8_t  ok_run;                   /* consecutive accepted fixes (stale clears at EXT_POS_TRUST_N) */
    uint8_t  o_n, o_head;              /* offset history fill and newest slot */
    float    o_x[EXT_POS_HIST_N], o_y[EXT_POS_HIST_N];  /* z - path(t_meas) of the last non-old fixes, cm */
    uint16_t n_disagree;               /* re-anchor runs held back by EXT_POS_AGREE_CM */
} ext_pos_stat_t;
typedef struct {                       /* Send_Task -> StabilizerTask handover, written under a critical section */
    float    x_cm, y_cm;
    uint32_t t_meas_ms;
    uint32_t seq;
} ext_pos_post_t;
extern volatile uint8_t        g_ext_pos_on;        /* 0 off (boot), 1 shadow, 2 offset outside the EKF, 3 in-EKF fix */
extern volatile float          g_ext_pos_delay_ms;  /* link delay added to the fix age; measure, default 0 */
extern volatile ext_pos_post_t g_ext_pos_post;
extern ext_pos_stat_t          g_ext_pos;           /* telemetry: g_ext_pos.e_x, .e_y, .n_ok, .n_rej, .age_ms */
void    ext_pos_ring_reset(ext_pos_ring_t *r);
void    ext_pos_ring_tick(ext_pos_ring_t *r, uint32_t now_ms, float ex, float ey);
uint8_t ext_pos_ring_at(const ext_pos_ring_t *r, uint32_t t_ms, float *ex, float *ey);
uint8_t ext_pos_fix(ext_pos_stat_t *s, const ext_pos_ring_t *r, uint32_t t_meas_ms, uint32_t now_ms, float zx, float zy);
void    ext_pos_stale_tick(ext_pos_stat_t *s, uint32_t now_ms);
uint8_t ext_pos_take(ext_pos_stat_t *s, uint8_t res);
uint8_t ext_pos_step(ext_pos_stat_t *s, uint8_t res, float *dx, float *dy);
void    ext_pos_spread(ext_pos_stat_t *s, float *dx, float *dy);
uint8_t ext_pos_decode(float val, float *x_cm, float *y_cm);
#define EXT_POS_FIX_OK        0U
#define EXT_POS_FIX_GATED     1U
#define EXT_POS_FIX_OLD       2U

#endif /* __EKF_OF_H__ */

