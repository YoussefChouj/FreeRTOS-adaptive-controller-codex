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

#endif
