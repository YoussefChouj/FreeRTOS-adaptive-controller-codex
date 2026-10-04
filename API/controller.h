/* Runtime-selectable controller layer between the PID inner loops and the motor mixer.
 *
 * Every entry adds a correction on top of the PID nominal output u_nom; CTRL_PID adds none.
 * Selection: the ground station writes g_ctrl_select_req (probe or command); Controller_CheckSwitch
 * applies it only while disarmed, so learned weights are never swapped in flight.
 * Slots with available == 0 are reserved for variants not implemented yet and are refused. */
#ifndef CONTROLLER_H
#define CONTROLLER_H

#include <stdint.h>

typedef enum {
    CTRL_PID = 0,
    CTRL_MRAC = 1,          /* mrac.c as compiled (USE_STRUCTURED/UNSTRUCTURED_UNCERTAINTY pick its basis) */
    CTRL_MRAC_STRUCT = 2,   /* reserved: runtime-separate structured MRAC */
    CTRL_MRAC_RBF = 3,      /* reserved: runtime-separate RBF-NN MRAC */
    CTRL_3LAYER = 4,        /* reserved: 3-layer stack */
    CTRL_MAX
} ctrl_id_e;

typedef enum { CTRL_AXIS_PITCH = 0, CTRL_AXIS_ROLL = 1, CTRL_AXIS_YAW = 2, CTRL_AXIS_Z = 3 } ctrl_axis_e;

typedef struct {
    void  (*reset)(void);
    float (*correction)(uint8_t axis);  /* added to u_nom; must be finite or it is dropped */
    uint8_t available;
} ctrl_ops_t;

#define CTRL_AXIS_MASK_ALL 0x0F

extern volatile uint8_t g_ctrl_select;      /* active controller (ctrl_id_e) */
extern volatile uint8_t g_ctrl_select_req;  /* requested controller; refused requests are reverted */
extern volatile uint8_t g_ctrl_axis_mask;   /* bit per ctrl_axis_e; a cleared bit sends pure PID on that axis */

void  Controller_Init(void);
void  Controller_CheckSwitch(uint8_t armed);
float Controller_Update(uint8_t axis, float u_nom);

/* Quad-X mixer (API/controller.c MIX_ROW table): one row per motor M1..M4, one column per mixer input. */
#define MIX_MOTORS  4
#define MIX_INPUTS  4
enum { MIX_THR = 0, MIX_PITCH = 1, MIX_ROLL = 2, MIX_YAW = 3 };   /* column index */
extern const float g_mix[MIX_MOTORS][MIX_INPUTS];

/* Motor command (CCR, before the Set_PWM_Motors clamp) of motor 0..3 from the four mixer inputs:
 * u_pitch = u_gyroy, u_roll = u_gyrox, u_yaw = g_yaw_mix_dir*u_gyroz. */
float Mix_Motor(uint8_t motor, float thr, float u_pitch, float u_roll, float u_yaw);
/* Sum over the motors of g_mix[m][input] * v[m] (the per-axis projection of a per-motor quantity). */
float Mix_Column(const float v[MIX_MOTORS], uint8_t input);
/* MRAC V2 saturation deficit in mixer units, def[ctrl_axis_e]: the part of each motor command outside
 * [2000, 4000] projected back per axis, mean over the motors (yaw scaled by yaw_dir = g_yaw_mix_dir). */
void  Mix_SatDeficit(float thr, float u_pitch, float u_roll, float u_yaw, float yaw_dir, float def[4]);

#endif /* CONTROLLER_H */
