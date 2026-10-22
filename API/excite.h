// ------------------------------------------------------------------------------
// Excitation pattern library (docs/design/2026-10-22-mrac-experiment-stack.md item 1)
// ------------------------------------------------------------------------------
// Orthogonal multisines added to the setpoints of one control layer:
//   outer = position / attitude-angle setpoints (loc*.Des, Z_posPID.Des, yaw/pitch/roll Des)
//   inner = velocity / rate setpoints (locxs/locys.Des, Z_ratePID.Des, gyro*.Des)
// Axis k owns harmonics h = k+1+6*(j0+j), j < nh, of one base period T, so the axes
// excited in one run never share a frequency (a response on axis j at axis k's
// harmonics is coupling). 24 rows = 6 axes x 2 layers x 2 speed sets (slow, fast).
// Schroeder phases, peak-normalised, 1 s cosine fades, per-row clamp. Any guard
// failure drops the output to 0 at once and latches the reason. Default = IDLE.
// Ticked at 200 Hz from StabilizerTask Compute_Motor.
// ------------------------------------------------------------------------------

#ifndef EXCITE_H
#define EXCITE_H

#include <stdint.h>

#define EXCITE_DT      0.005f   /* control period (200 Hz), same as SYSID_DT */
#define EXCITE_FADE_S  1.0f     /* cosine fade in and out */

typedef enum {
    EXC_X = 0, EXC_Y = 1, EXC_Z = 2, EXC_YAW = 3, EXC_PITCH = 4, EXC_ROLL = 5, EXC_AXES = 6
} Excite_Axis_e;

typedef enum { EXC_OUTER = 0, EXC_INNER = 1 } Excite_Layer_e;
typedef enum { EXC_SLOW = 0, EXC_FAST = 1 } Excite_Speed_e;
typedef enum { EXC_IDLE = 0, EXC_RUNNING = 1 } Excite_State_e;

/* Excite_Start refusals and latched abort reasons (excite_reason). */
typedef enum {
    EXC_OK = 0,
    EXC_R_ARGS = 1,       /* bad layer / speed / mask / n_periods */
    EXC_R_DISARMED = 2,
    EXC_R_MODE = 3,       /* not FlyMode_SDK */
    EXC_R_GROUND = 4,     /* not flying */
    EXC_R_RC = 5,         /* RC stick active */
    EXC_R_ALT = 6,
    EXC_R_XY = 7,         /* left the soft box around the start position */
    EXC_R_ANGLE = 8,
    EXC_R_SYSID = 9,      /* a SysID run is active */
    EXC_R_CMD = 10,       /* aborted by the ground station */
    EXC_R_DONE = 11       /* all periods flown */
} Excite_Reason_e;

/* Logged state (stream_log symbols). */
extern volatile uint8_t excite_state;     /* Excite_State_e */
extern volatile uint8_t excite_reason;    /* last Excite_Reason_e */
extern volatile uint8_t excite_layer, excite_speed, excite_mask;
extern volatile uint16_t excite_period;   /* index of the period being flown */
extern volatile float excite_out[EXC_AXES];

uint8_t Excite_Start(uint8_t layer, uint8_t speed, uint8_t mask, uint8_t n_periods);
void Excite_Abort(uint8_t reason);
void Excite_Update(uint8_t flying);
float Excite_Get(uint8_t axis, uint8_t layer);   /* 0 unless running on that axis and layer */

#endif
