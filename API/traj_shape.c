/**
 * @module     traj_shape.c
 * @subsystem  guidance
 * @purpose    Circle / figure-8 shapes for the AutoflyTask presets plus yaw-follow heading and slew.
 *             Pure math, host-tested by tests/firmware_host/test_traj_shape.c.
 */
#include "traj_shape.h"
#include <math.h>
#include <stddef.h>

#define TRAJ_SHAPE_PI   3.14159265f
#define TRAJ_SHAPE_DTH  0.01f   /* central-difference step in path angle [rad] */

void TrajShape_Offset(const traj_shape_t *p, float th, float *dx, float *dy, float *dz)
{
    float s = sinf(th);
    float c = cosf(th);
    float y;

    if (p == NULL || dx == NULL || dy == NULL || dz == NULL) {
        return;
    }
    *dz = 0.0f;
    if (p->id == 3U) {
        /* circle through the start point, centre circ_r_cm toward -x, leaves toward +y */
        *dx = p->circ_r_cm * (c - 1.0f);
        *dy = p->circ_r_cm * s;
        return;
    }
    /* figure-8 from the start point, leaves toward +y */
    if (p->f8_type == 1U) {
        *dx = 0.5f * p->f8_a_cm * sinf(2.0f * th);
        y = p->f8_a_cm * s;
    } else {
        *dx = p->f8_a_cm * (c / (1.0f + s * s) - 1.0f);
        y = p->f8_a_cm * s * c / (1.0f + s * s);
    }
    *dy = y * p->tilt_c;
    *dz = y * p->tilt_s;
}

float TrajShape_TangentDeg(const traj_shape_t *p, float th, float om_sign)
{
    float x0 = 0.0f, y0 = 0.0f, x1 = 0.0f, y1 = 0.0f, z = 0.0f;
    float tx, ty;

    TrajShape_Offset(p, th - TRAJ_SHAPE_DTH, &x0, &y0, &z);
    TrajShape_Offset(p, th + TRAJ_SHAPE_DTH, &x1, &y1, &z);
    tx = x1 - x0;
    ty = y1 - y0;
    if (om_sign < 0.0f) {
        tx = -tx;
        ty = -ty;
    }
    return atan2f(ty, tx) * (180.0f / TRAJ_SHAPE_PI);
}

float TrajShape_WrapDeg(float deg)
{
    deg = fmodf(deg, 360.0f);   /* not a loop: an inf input gives NaN instead of hanging the task */
    if (deg >= 180.0f) {
        deg -= 360.0f;
    } else if (deg < -180.0f) {
        deg += 360.0f;
    }
    return deg;
}

float TrajShape_YawStep(float cmd, float target, float max_step)
{
    float step = TrajShape_WrapDeg(target - cmd);

    if (step > max_step) {
        step = max_step;
    } else if (step < -max_step) {
        step = -max_step;
    }
    return TrajShape_WrapDeg(cmd + step);
}
