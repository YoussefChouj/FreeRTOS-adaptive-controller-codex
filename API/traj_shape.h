#ifndef TRAJ_SHAPE_H
#define TRAJ_SHAPE_H

#include <stdint.h>

/* Circle / figure-8 shapes for the AutoflyTask trajectory presets, plus the yaw-follow heading and slew.
 * Pure math (no globals) so tests/firmware_host/test_traj_shape.c can check it on the host.
 * Frame: loc x/y [cm] and z up [cm]; angles in degrees, x toward y positive (the yaw sense). */

typedef struct {
    uint8_t id;        /* 3 circle, 4 figure-8 */
    float   circ_r_cm;
    uint8_t f8_type;   /* 1 Gerono (crosses at the start point), 0 Bernoulli (starts at the +x tip) */
    float   f8_a_cm;
    float   tilt_s;    /* sin / cos of the figure-8 tilt about x: z = y * tilt_s, y = y * tilt_c */
    float   tilt_c;
} traj_shape_t;

/* Offset from the start point at path angle th [rad]. */
void TrajShape_Offset(const traj_shape_t *p, float th, float *dx, float *dy, float *dz);

/* Heading of the x/y direction of travel at path angle th [deg, -180..180]; om_sign < 0 when th runs backward. */
float TrajShape_TangentDeg(const traj_shape_t *p, float th, float om_sign);

/* Wraps to [-180, 180) [deg]. */
float TrajShape_WrapDeg(float deg);

/* One slew step of a heading command toward target, the short way round, at most max_step [deg]. */
float TrajShape_YawStep(float cmd, float target, float max_step);

/* ------------------------------------------------------------------------------
 * n26-B: Slow clock, minimum-jerk (quintic) segments, analytic derivatives, and waypoint arrival log
 * ------------------------------------------------------------------------------ */

#define TRAJ_SLOW_RECOVER_PER_S  1.0f   /* PROPOSED max +ds/dt recovery rate [1/s] (no jerk on recovery) */
#define TRAJ_WP_MAX              16U    /* max tracked waypoints per preset/trajectory */

/* Slow-clock time-scale update:
 *   s_raw(err) = 1 for err <= e_lo, 0 for err >= e_hi, C2 quintic smoothstep between e_lo and e_hi.
 *   Falls immediately along the smooth curve when err grows; recovers at rate <= recover_rate_per_s [1/s].
 *   Writes *ds_dt (0 when ds_dt is NULL) and returns the new s in [0, 1]. */
float TrajShape_SlowClockStep(float s_prev, float err_cm, float e_lo_cm, float e_hi_cm,
                              float recover_rate_per_s, float dt_s, float *ds_dt);

/* Scale path velocity and acceleration derivatives by slow-clock factor s and rate ds_dt:
 *   vff = s * v_path,  aff = s^2 * a_path + ds_dt * v_path. */
void TrajShape_ScaleFF(float s, float ds_dt,
                       float vx_path, float vy_path, float ax_path, float ay_path,
                       float *vff_x, float *vff_y, float *aff_x, float *aff_y);

/* 1D minimum-jerk (quintic) boundary-value polynomial on t in [0, T_s]:
 * matches (p0, v0, a0) at t = 0 and (p1, v1, a1) at t = T_s. */
void TrajShape_Quintic1D(float t_s, float T_s,
                         float p0, float v0, float a0,
                         float p1, float v1, float a1,
                         float *pos, float *vel, float *acc);

/* 3D rest-to-rest (or general boundary) minimum-jerk segment on t in [0, T_s]. */
void TrajShape_QuinticSegment3D(float t_s, float T_s,
                                const float p0[3], const float v0[3], const float a0[3],
                                const float p1[3], const float v1[3], const float a1[3],
                                float pos[3], float vel[3], float acc[3]);

/* Time-warp w(t), dw/dt, d2w/dt2 for preset move_s and ramp_s:
 *   profile 0: constant speed (w = t, dw = 1, ddw = 0)
 *   profile 1: cosine velocity ramp over ramp_s at both ends
 *   profile 2: quintic minimum-jerk velocity ramp over ramp_s at both ends (continuous accel at 0, R, T-R, T) */
void TrajShape_WarpDerivs(uint8_t profile, float t_s, float move_s, float ramp_s,
                          float *w_s, float *dw_dt, float *ddw_dt2);

/* Analytic position offset [cm], velocity [cm/s], and acceleration [cm/s^2] for circle (id 3) and figure-8 (id 4)
 * at path angle th [rad], path angular rate dth_dt [rad/s], and path angular acceleration ddth_dt2 [rad/s^2]. */
void TrajShape_Derivs(const traj_shape_t *p, float th, float dth_dt, float ddth_dt2,
                      float pos_cm[3], float vel_cms[3], float acc_cms2[3]);

/* Waypoint arrival log entry and tracker */
typedef struct {
    uint8_t wp_idx;
    uint8_t arrived;
    float   wx_cm, wy_cm, wz_cm;
    float   t_plan_s;
    float   t_actual_s;
    float   peak_err_cm;
    float   time_lost_s;   /* integral of (1 - s) * dt [s] */
    float   int_s_s;       /* integral of s * dt [s] */
} traj_wp_entry_t;

typedef struct {
    uint8_t count;
    uint8_t active_seg;
    float   r_cm;
    float   elapsed_s;
    float   int_s_accum;
    float   time_lost_accum;
    float   seg_peak_err_cm;
    traj_wp_entry_t entries[TRAJ_WP_MAX];
} traj_wp_tracker_t;

/* Initialize waypoint tracker with n_wp planned waypoints (wx_cm, wy_cm, wz_cm, t_plan_s) and arrival radius r_cm. */
void TrajWp_Init(traj_wp_tracker_t *trk, float r_cm, uint8_t n_wp,
                 const float (*wx_wy_wz_tplan)[4]);

/* Build default preset waypoints (4 per lap for circle/figure-8, 2 for step) into trk. */
void TrajWp_InitPreset(traj_wp_tracker_t *trk, const traj_shape_t *shape,
                       uint8_t profile, float move_s, float ramp_s, float laps,
                       float step_cm, uint8_t axis, float r_cm);

/* Advance waypoint tracker by one tick dt_s at slow-clock factor s.
 * Returns 1 if an arrival event fired on this tick (and writes *arrived_idx), 0 otherwise. */
uint8_t TrajWp_Tick(traj_wp_tracker_t *trk, float dt_s, float s,
                    float pos_x_cm, float pos_y_cm, float ref_x_cm, float ref_y_cm,
                    uint8_t *arrived_idx);

#endif
