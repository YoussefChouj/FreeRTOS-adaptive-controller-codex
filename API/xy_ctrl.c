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

/* the j-th oldest command still in the pipe (j = 0 acts next) */
static float pipe_at(const XyCtrlState *s, int j)
{
	int i = s->head + j;
	return s->pipe[i >= XYC_D ? i - XYC_D : i];
}

/* firmware PID EMin rule: integrate only near the target */
static void integrate(XyCtrlState *s, float pos, float des)
{
	float e = pos - des;
	if (fabsf(e) < XYC_INT_BAND)
		s->z = clampf(s->z + XYC_DT * e, -XYC_INT_MAX, XYC_INT_MAX);
}

/* error state at the moment the next command starts acting: [e_pos, e_vel, a - a_ff, integral] */
static void predicted_error(const XyCtrlState *s, float des, float pos, float vel, float vff, float x[4])
{
	float x0[3] = { pos, vel, s->a_est };
	float xp[3];
	int r, j;
	for (r = 0; r < 3; r++) {
		float acc = XYC_PHI[r][0] * x0[0] + XYC_PHI[r][1] * x0[1] + XYC_PHI[r][2] * x0[2];
		for (j = 0; j < XYC_D; j++)
			acc += XYC_GAM[r][j] * pipe_at(s, j);
		xp[r] = acc;
	}
	x[0] = xp[0] - des;
	x[1] = xp[1] - vff;
	x[2] = xp[2] - XYC_DRAG * vff / XYC_K;
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
	s->z2 += XYC_DT * (s->z3 - XYC_ADRC_B2 * e + XYC_ADRC_B0 * s->pipe[s->head]);
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
}

float XyCtrl_Step(XyCtrlState *s, uint8_t kind, float des, float pos, float vel, float vff)
{
	float x[4], uff = XYC_DRAG * vff / XYC_K;
	switch (kind) {
	case XY_CTRL_ADRC: {
		float u0;
		eso_update(s, pos);
		u0 = XYC_ADRC_WC * XYC_ADRC_WC * (des - s->z1) + 2.0f * XYC_ADRC_WC * (vff - s->z2);
		return clampf((u0 - s->z3) / XYC_ADRC_B0, -XYC_AMAX, XYC_AMAX);
	}
	case XY_CTRL_LQR:
		integrate(s, pos, des);
		predicted_error(s, des, pos, vel, vff, x);
		return clampf(uff - (XYC_LQR_K[0] * x[0] + XYC_LQR_K[1] * x[1] + XYC_LQR_K[2] * x[2] + XYC_LQR_K[3] * x[3]),
		              -XYC_AMAX, XYC_AMAX);
	case XY_CTRL_MPC:
		integrate(s, pos, des);
		predicted_error(s, des, pos, vel, vff, x);
		return clampf(mpc_solve(s, x, uff) + uff, -XYC_AMAX, XYC_AMAX);
	default:
		return 0.0f;
	}
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
	s->a_est = XYC_AL * s->a_est + (1.0f - XYC_AL) * s->pipe[s->head];
	s->pipe[s->head] = u_applied;
	s->head = (uint8_t)(s->head + 1U >= XYC_D ? 0U : s->head + 1U);
	s->u_prev = u_applied;
}
