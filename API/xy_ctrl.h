// ------------------------------------------------------------------------------
// xy outer-loop controllers (2026-10-14): ADRC, LQR, MPC on the identified xy plant
// ------------------------------------------------------------------------------
// Port of ground_station/analysis/xy_ctrl.py (the sim/replay reference; gains and model matrices
// are generated into xy_ctrl_gains.h by `python -m ground_station.analysis.xy_ctrl`). Each controller
// replaces the locx + locxs PID cascade output: position cm, velocity cm/s, reference velocity
// feed-forward cm/s -> horizontal accel command cm/s^2 (the locxsPID.U units), one call per 10 ms.
// Kind 0 = the PID cascade (this module does nothing).
// ------------------------------------------------------------------------------

#ifndef XY_CTRL_H
#define XY_CTRL_H

#include <stdint.h>

#define XY_CTRL_PID   0U
#define XY_CTRL_ADRC  1U
#define XY_CTRL_LQR   2U
#define XY_CTRL_MPC   3U

#define XY_CTRL_DMAX  48     /* command pipe capacity, 10 ms ticks (the model delay must fit) */
#define XY_CTRL_NMAX  10     /* MPC horizon blocks */

typedef struct {
	float pipe[XY_CTRL_DMAX];   /* commands not yet acting; pipe[head] = oldest (acts next) */
	uint8_t head;
	float a_est;                /* plant lag state (the accel the oldest commands have built) */
	float z;                    /* integral of the position error, cm s (LQR/MPC) */
	float z1, z2, z3;           /* ADRC ESO: position, velocity, total disturbance */
	uint8_t init;               /* ADRC: z1 seeded from the first position */
	float plan[XY_CTRL_NMAX];   /* MPC warm start, deviation from u_ff */
	float u_prev;               /* MPC: last applied command */
} XyCtrlState;

void XyCtrl_Reset(XyCtrlState *s);

/* Smith-predictor delay, 10 ms ticks (clamped 1..XY_CTRL_DMAX; default XYC_D). Rebuilds the prediction matrices;
 * shared by both axes, so call it on the ground and Reset every state afterwards (the pipe length changes). */
void XyCtrl_SetDelay(int d);
int XyCtrl_Delay(void);

/* One controller tick, without committing: returns the accel command cm/s^2 (clamped to the tilt cap). */
float XyCtrl_Step(XyCtrlState *s, uint8_t kind, float des, float pos, float vel, float vff);

/* Observer-only tick while another loop flies (RC stick, PID fallback): keeps the ESO running and
 * clears the integral and the MPC plan, so a later Step starts bumpless. Commit still follows. */
void XyCtrl_Track(XyCtrlState *s, uint8_t kind, float pos);

/* Every tick after Step or Track: the command actually applied enters the pipe. */
void XyCtrl_Commit(XyCtrlState *s, float u_applied);

#endif
