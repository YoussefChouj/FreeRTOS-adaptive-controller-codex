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

/* ------------------------------------------------------------------------------
 * n26-B: Predictor reference lead, acceleration feedforward, and runtime-writable xy gains
 * ------------------------------------------------------------------------------
 * Note on MPC matrices: XYC_MPC_H[10][10] and XYC_MPC_F[10][4] (140 floats + step size) are condensed QP
 * matrices in flash RO-data; changing MPC cost weights requires running `python -m ground_station.analysis.xy_ctrl`
 * and reflashing. Model/delay changes (k, al, drag, d) DO rebuild the Smith predictor matrices (s_phi, s_gam)
 * used by both LQR and MPC at runtime while disarmed.
 * ------------------------------------------------------------------------------ */

#define XYC_VAR_DELAY_TICKS  47U
#define XYC_VAR_PRED_LEAD    74U
#define XYC_VAR_LQR_K0       75U
#define XYC_VAR_LQR_K1       76U
#define XYC_VAR_LQR_K2       77U
#define XYC_VAR_LQR_K3       78U
#define XYC_VAR_ADRC_WC      79U
#define XYC_VAR_ADRC_B0      80U
#define XYC_VAR_MODEL_K      81U
#define XYC_VAR_MODEL_AL     82U
#define XYC_VAR_MODEL_DRAG   83U

void XyCtrl_SetPredLead(uint8_t on);
uint8_t XyCtrl_PredLead(void);

/* Compute the 4-state predicted error [e_pos, e_vel, a - a_ff, integral].
 * When pred_lead is on: uses fut_ref [des_d, vff_d, aff_d] if non-NULL, else Taylor-extrapolates des, vff by d*dt. */
void XyCtrl_PredictedError(const XyCtrlState *s, float des, float pos, float vel, float vff,
                           float aff, const float *fut_ref, float x[4]);

/* Extended step with acceleration feedforward aff [cm/s^2] and optional future reference fut_ref[3] at t + d*dt. */
float XyCtrl_StepEx(XyCtrlState *s, uint8_t kind, float des, float pos, float vel,
                    float vff, float aff, const float *fut_ref);

/* Runtime gain and model configuration (all writes refused with -1 when armed != 0; return 0 on success). */
void XyCtrl_ResetGainsToDefault(void);
int XyCtrl_SetLqrK(uint8_t armed, const float k[4]);
int XyCtrl_SetAdrcGains(uint8_t armed, float wc, float b0);
int XyCtrl_SetModelParams(uint8_t armed, float k, float al, float drag, int d);
int XyCtrl_WriteParam(uint8_t armed, uint8_t var_id, float val);

void XyCtrl_GetLqrK(float k[4]);
void XyCtrl_GetAdrcGains(float *wc, float *b0);
void XyCtrl_GetModelParams(float *k, float *al, float *drag, int *d);

#endif
