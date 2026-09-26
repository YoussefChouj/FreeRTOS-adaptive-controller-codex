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

extern volatile uint8_t g_ctrl_select;      /* active controller (ctrl_id_e) */
extern volatile uint8_t g_ctrl_select_req;  /* requested controller; refused requests are reverted */
extern volatile uint8_t g_ctrl_axis_mask;   /* bit per ctrl_axis_e; a cleared bit sends pure PID on that axis */

void  Controller_Init(void);
void  Controller_CheckSwitch(uint8_t armed);
float Controller_Update(uint8_t axis, float u_nom);

#endif /* CONTROLLER_H */
