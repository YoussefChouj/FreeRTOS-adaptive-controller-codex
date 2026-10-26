/**
 * @module     xy_ctrl.c
 * @subsystem  control
 * @purpose    xy outer-loop ADRC / LQR / MPC (kind via VAR 44 g_xy_ctrl_kind);
 *             C port of ground_station/analysis/xy_ctrl.py, gains in xy_ctrl_gains.h.
 */
// ------------------------------------------------------------------------------
// xy outer-loop controllers: ADRC, LQR (Smith predictor), MPC (Smith predictor + FISTA QP)
// ------------------------------------------------------------------------------
// Line-for-line port of ground_station/analysis/xy_ctrl.py (Adrc, Lqr, Mpc, Predictor), checked
// against it by ground_station/analysis/tests/test_xy_ctrl_host.py. Model and gains: xy_ctrl_gains.h.
// ------------------------------------------------------------------------------

#include "xy_ctrl.h"
#include "xy_ctrl_gains.h"
#include <math.h>
#include <string.h>

#if XYC_D < 1 || XYC_D > XY_CTRL_DMAX
#error "xy_ctrl: model delay outside the command pipe"
#endif
#if XYC_MPC_N > XY_CTRL_NMAX
#error "xy_ctrl: MPC horizon too long"
#endif

static float clampf(float v, float lo, float hi)
{
	return v < lo ? lo : (v > hi ? hi : v);
}

/* Smith-predictor delay (ticks), runtime model parameters, runtime gains, and prediction matrices.
 * Defaults equal xy_ctrl_gains.h constants exactly. */
static int s_d = XYC_D;
static float s_k = XYC_K;
static float s_al = XYC_AL;
static float s_drag = XYC_DRAG;
static float s_adrc_wc = XYC_ADRC_WC;
static float s_adrc_b0 = XYC_ADRC_B0;
static float s_lqr_k[4];
static uint8_t s_lqr_init;
static uint8_t s_pred_lead;
static float s_phi[3][3];
static float s_gam[3][XY_CTRL_DMAX];
static uint8_t s_built;

static void ensure_gains_init(void)
{
	if (!s_lqr_init) {
		memcpy(s_lqr_k, XYC_LQR_K, sizeof(s_lqr_k));
		s_lqr_init = 1U;
	}
}

void XyCtrl_SetDelay(int d)
{
	/* one tick of [pos, vel, a], the xy_ctrl.py model_ad order: a, then v with the new a, then pos */
	const float dt = XYC_DT, al = s_al, k = s_k, dr = s_drag;
	const float A[3][3] = { { 1.0f, dt * (1.0f - dr * dt), dt * dt * k * al },
	                        { 0.0f, 1.0f - dr * dt,        dt * k * al },
	                        { 0.0f, 0.0f,                  al } };
	const float B[3] = { dt * dt * k * (1.0f - al), dt * k * (1.0f - al), 1.0f - al };
	float t[3];
	int r, c, j, i;
	ensure_gains_init();
	s_d = d < 1 ? 1 : (d > XY_CTRL_DMAX ? XY_CTRL_DMAX : d);
	memset(s_phi, 0, sizeof(s_phi));
	memset(s_gam, 0, sizeof(s_gam));
	for (r = 0; r < 3; r++)
		s_phi[r][r] = 1.0f;
	for (j = 0; j < s_d; j++) {   /* x_d = A^d x0 + sum_j A^(d-1-j) B u_j (xy_ctrl.py Predictor) */
		for (i = 0; i < j; i++) {
			for (r = 0; r < 3; r++)
				t[r] = A[r][0] * s_gam[0][i] + A[r][1] * s_gam[1][i] + A[r][2] * s_gam[2][i];
			for (r = 0; r < 3; r++)
				s_gam[r][i] = t[r];
		}
		for (r = 0; r < 3; r++)
			s_gam[r][j] = B[r];
		for (c = 0; c < 3; c++) {
			for (r = 0; r < 3; r++)
				t[r] = A[r][0] * s_phi[0][c] + A[r][1] * s_phi[1][c] + A[r][2] * s_phi[2][c];
			for (r = 0; r < 3; r++)
				s_phi[r][c] = t[r];
		}
	}
	s_built = 1U;
}

int XyCtrl_Delay(void)
{
	return s_d;
}

void XyCtrl_SetPredLead(uint8_t on)
{
	s_pred_lead = (on != 0U) ? 1U : 0U;
}

uint8_t XyCtrl_PredLead(void)
{
	return s_pred_lead;
}

void XyCtrl_ResetGainsToDefault(void)
{
	s_k = XYC_K;
	s_al = XYC_AL;
	s_drag = XYC_DRAG;
	s_adrc_wc = XYC_ADRC_WC;
	s_adrc_b0 = XYC_ADRC_B0;
	memcpy(s_lqr_k, XYC_LQR_K, sizeof(s_lqr_k));
	s_lqr_init = 1U;
	s_pred_lead = 0U;
	XyCtrl_SetDelay(XYC_D);
}

int XyCtrl_SetLqrK(uint8_t armed, const float k[4])
{
	if (armed != 0U || k == NULL)
		return -1;
	ensure_gains_init();
	memcpy(s_lqr_k, k, sizeof(s_lqr_k));
	return 0;
}

int XyCtrl_SetAdrcGains(uint8_t armed, float wc, float b0)
{
	if (armed != 0U || !(wc > 0.0f) || !(b0 > 0.0f))
		return -1;
	ensure_gains_init();
	s_adrc_wc = wc;
	s_adrc_b0 = b0;
	return 0;
}

int XyCtrl_SetModelParams(uint8_t armed, float k, float al, float drag, int d)
{
	if (armed != 0U || !(k > 0.0f) || !(al > 0.0f && al < 1.0f) || !(drag >= 0.0f))
		return -1;
	ensure_gains_init();
	s_k = k;
	s_al = al;
	s_drag = drag;
	XyCtrl_SetDelay(d);
	return 0;
}

int XyCtrl_WriteParam(uint8_t armed, uint8_t var_id, float val)
{
	if (armed != 0U)
		return -1;
	ensure_gains_init();
	switch (var_id) {
	case XYC_VAR_DELAY_TICKS:
		XyCtrl_SetDelay((int)val);
		return 0;
	case XYC_VAR_PRED_LEAD:
		XyCtrl_SetPredLead((uint8_t)(val != 0.0f));
		return 0;
	case XYC_VAR_LQR_K0:
	case XYC_VAR_LQR_K1:
	case XYC_VAR_LQR_K2:
	case XYC_VAR_LQR_K3:
		s_lqr_k[var_id - XYC_VAR_LQR_K0] = val;
		return 0;
	case XYC_VAR_ADRC_WC:
		if (!(val > 0.0f)) return -1;
		s_adrc_wc = val;
		return 0;
	case XYC_VAR_ADRC_B0:
		if (!(val > 0.0f)) return -1;
		s_adrc_b0 = val;
		return 0;
	case XYC_VAR_MODEL_K:
		if (!(val > 0.0f)) return -1;
		s_k = val;
		XyCtrl_SetDelay(s_d);
		return 0;
	case XYC_VAR_MODEL_AL:
		if (!(val > 0.0f && val < 1.0f)) return -1;
		s_al = val;
		XyCtrl_SetDelay(s_d);
		return 0;
	case XYC_VAR_MODEL_DRAG:
		if (!(val >= 0.0f)) return -1;
		s_drag = val;
		XyCtrl_SetDelay(s_d);
		return 0;
	default:
		return -1;
	}
}

void XyCtrl_GetLqrK(float k[4])
{
	ensure_gains_init();
	if (k != NULL)
		memcpy(k, s_lqr_k, sizeof(s_lqr_k));
}

void XyCtrl_GetAdrcGains(float *wc, float *b0)
{
	ensure_gains_init();
	if (wc != NULL) *wc = s_adrc_wc;
	if (b0 != NULL) *b0 = s_adrc_b0;
}

void XyCtrl_GetModelParams(float *k, float *al, float *drag, int *d)
{
	ensure_gains_init();
	if (k != NULL) *k = s_k;
	if (al != NULL) *al = s_al;
	if (drag != NULL) *drag = s_drag;
	if (d != NULL) *d = s_d;
}

/* the j-th oldest command still in the pipe (j = 0 acts next) */
static float pipe_at(const XyCtrlState *s, int j)
{
	int i = s->head + j;
	return s->pipe[i >= s_d ? i - s_d : i];
}

/* firmware PID EMin rule: integrate only near the target */
static void integrate(XyCtrlState *s, float pos, float des)
{
	float e = pos - des;
	if (fabsf(e) < XYC_INT_BAND)
		s->z = clampf(s->z + XYC_DT * e, -XYC_INT_MAX, XYC_INT_MAX);
}

/* error state at the moment the next command starts acting: [e_pos, e_vel, a - a_ff, integral] */
void XyCtrl_PredictedError(const XyCtrlState *s, float des, float pos, float vel, float vff,
                           float aff, const float *fut_ref, float x[4])
{
	float x0[3] = { pos, vel, s->a_est };
	float xp[3];
	float des_ref = des;
	float vff_ref = vff;
	float aff_ref = aff;
	int r, j;
	if (!s_built)
		XyCtrl_SetDelay(s_d);
	for (r = 0; r < 3; r++) {
		float acc = s_phi[r][0] * x0[0] + s_phi[r][1] * x0[1] + s_phi[r][2] * x0[2];
		for (j = 0; j < s_d; j++)
			acc += s_gam[r][j] * pipe_at(s, j);
		xp[r] = acc;
	}
	if (s_pred_lead != 0U) {
		if (fut_ref != NULL) {
			des_ref = fut_ref[0];
			vff_ref = fut_ref[1];
			aff_ref = fut_ref[2];
		} else {
			float tau_d = (float)s_d * XYC_DT;
			des_ref = des + tau_d * vff + 0.5f * tau_d * tau_d * aff;
			vff_ref = vff + tau_d * aff;
		}
	}
	x[0] = xp[0] - des_ref;
	x[1] = xp[1] - vff_ref;
	x[2] = xp[2] - ((aff_ref != 0.0f) ? ((aff_ref + s_drag * vff_ref) / s_k) : (s_drag * vff_ref / s_k));
	x[3] = s->z;
}

static void eso_update(XyCtrlState *s, float pos)
{
	float e;
	if (!s->init) {
		s->z1 = pos;
		s->init = 1U;
	}
	e = s->z1 - pos;
	s->z1 += XYC_DT * (s->z2 - XYC_ADRC_B1 * e);
	s->z2 += XYC_DT * (s->z3 - XYC_ADRC_B2 * e + s_adrc_b0 * s->pipe[s->head]);
	s->z3 += XYC_DT * (-XYC_ADRC_B3 * e);
}

static float mpc_solve(XyCtrlState *s, const float x[4], float uff)
{
	float lin[XYC_MPC_N], u[XYC_MPC_N], y[XYC_MPC_N], un[XYC_MPC_N];
	float lo = -XYC_AMAX - uff, hi = XYC_AMAX - uff, t = 1.0f;
	int i, j, it;
	for (i = 0; i < XYC_MPC_N; i++) {
		lin[i] = XYC_MPC_F[i][0] * x[0] + XYC_MPC_F[i][1] * x[1] + XYC_MPC_F[i][2] * x[2] + XYC_MPC_F[i][3] * x[3];
		u[i] = y[i] = s->plan[i];
	}
	lin[0] -= XYC_MPC_RD * (s->u_prev - uff);   /* input-change cost on the first block */
	for (it = 0; it < XYC_MPC_ITERS; it++) {
		float t_new = 0.5f * (1.0f + sqrtf(1.0f + 4.0f * t * t));
		float mom = (t - 1.0f) / t_new;
		for (i = 0; i < XYC_MPC_N; i++) {
			float g = lin[i];
			for (j = 0; j < XYC_MPC_N; j++)
				g += XYC_MPC_H[i][j] * y[j];
			un[i] = clampf(y[i] - XYC_MPC_STEP * g, lo, hi);
		}
		for (i = 0; i < XYC_MPC_N; i++) {
			y[i] = un[i] + mom * (un[i] - u[i]);
			u[i] = un[i];
		}
		t = t_new;
	}
	memcpy(s->plan, u, sizeof(u));
	return u[0];
}

void XyCtrl_Reset(XyCtrlState *s)
{
	memset(s, 0, sizeof(*s));
	ensure_gains_init();
	if (!s_built)
		XyCtrl_SetDelay(s_d);
}

float XyCtrl_StepEx(XyCtrlState *s, uint8_t kind, float des, float pos, float vel,
                    float vff, float aff, const float *fut_ref)
{
	float x[4];
	float uff;
	ensure_gains_init();
	uff = (aff != 0.0f) ? ((aff + s_drag * vff) / s_k) : (s_drag * vff / s_k);
	switch (kind) {
	case XY_CTRL_ADRC: {
		float u0;
		eso_update(s, pos);
		u0 = s_adrc_wc * s_adrc_wc * (des - s->z1) + 2.0f * s_adrc_wc * (vff - s->z2);
		if (aff != 0.0f)
			u0 += aff;
		return clampf((u0 - s->z3) / s_adrc_b0, -XYC_AMAX, XYC_AMAX);
	}
	case XY_CTRL_LQR:
		integrate(s, pos, des);
		XyCtrl_PredictedError(s, des, pos, vel, vff, aff, fut_ref, x);
		return clampf(uff - (s_lqr_k[0] * x[0] + s_lqr_k[1] * x[1] + s_lqr_k[2] * x[2] + s_lqr_k[3] * x[3]),
		              -XYC_AMAX, XYC_AMAX);
	case XY_CTRL_MPC:
		integrate(s, pos, des);
		XyCtrl_PredictedError(s, des, pos, vel, vff, aff, fut_ref, x);
		return clampf(mpc_solve(s, x, uff) + uff, -XYC_AMAX, XYC_AMAX);
	default:
		return 0.0f;
	}
}

float XyCtrl_Step(XyCtrlState *s, uint8_t kind, float des, float pos, float vel, float vff)
{
	return XyCtrl_StepEx(s, kind, des, pos, vel, vff, 0.0f, NULL);
}

void XyCtrl_Track(XyCtrlState *s, uint8_t kind, float pos)
{
	if (kind == XY_CTRL_ADRC)
		eso_update(s, pos);
	s->z = 0.0f;
	memset(s->plan, 0, sizeof(s->plan));
}

void XyCtrl_Commit(XyCtrlState *s, float u_applied)
{
	s->a_est = s_al * s->a_est + (1.0f - s_al) * s->pipe[s->head];
	s->pipe[s->head] = u_applied;
	s->head = (uint8_t)(s->head + 1 >= s_d ? 0 : s->head + 1);
	s->u_prev = u_applied;
}
