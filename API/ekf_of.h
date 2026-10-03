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
    float R_zupt;
    /* Innovation */
    float innov_x;
    float innov_y;
    uint8_t inited;
    /* OF innovation gate (PX4 EKF2_OF_GATE style): skip a sample when innov^2 > of_gate^2 * S */
    float of_gate;      /* sigmas; 0 = gate off */
    uint32_t rej_x;     /* rejected OF samples since init */
    uint32_t rej_y;
} EkfOf_t;

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

/** Zero velocity update (on ground). */
extern void EkfOf_UpdateZeroVel(EkfOf_t *e);

/** Reset position states to zero (call on Reset_World_Origin).
 *  Keeps vel and bias estimates intact. */
extern void EkfOf_ResetPos(EkfOf_t *e);

/** Zero both OF-bias states (bof_x, bof_y) and set their variance to var
 *  with no cross-covariance. Called on ARM and on the handheld-test edge. */
extern void EkfOf_ResetBias(EkfOf_t *e, float var);

/* bof variance at ARM, (m/s)^2. 2.5e-5 = (0.5 cm/s)^2: OF is integer cm/s and
 * good hovers read ~0-1 cm/s raw, so a stale resting value must not be learned
 * back into bof in the first seconds of flight. */
#define EKF_OF_BOF_ARM_VAR      2.5e-5f

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

#endif /* __EKF_OF_H__ */

