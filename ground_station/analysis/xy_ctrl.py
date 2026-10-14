"""Outer xy controllers for the xy_tune sim (2026-10-14): ADRC, LQR, MPC on the identified plant.

Each replaces the locx + locxs cascade: `step(des cm, pos cm, vel cm/s, vff cm/s) -> accel cm/s^2` on B numpy
columns (one per simulated plant), the same interface as xy_tune.FwCascade. Model = xy_tune's plant:
    a = first-order lag (tau) of the command delayed d ticks,  v' = k*a - drag*v + bias  (cm, s; 100 Hz).
The nominal model (`nominal`) is the mean of the fitted plants (delay: their mean, in 10 ms ticks).

- ADRC: linear ADRC (Gao 2003), 2nd-order ESO on position, bandwidth parameterized (b1 3wo, b2 3wo^2, b3 wo^3),
  the ESO fed the command delayed by the model delay; u = (wc^2 (r - z1) + 2 wc (vff - z2) - z3) / b0.
- LQR: Smith predictor (state when the next command starts acting, from measured pos/vel, the lag state run on the
  command history, and the d commands still in the pipe) + DARE gain on [e_pos, e_vel, a, integral e_pos], compared
  with the CURRENT reference (so the response lags the reference by the plant delay instead of overshooting it).
- MPC: same predictor and state; horizon N blocks of M ticks, cost on e_pos/e_vel/integral, input and input-change,
  box |u| <= tilt cap; QP solved by FISTA projected gradient, fixed iterations, warm start (firmware-sized).

Weights and bandwidths are tuned by `xy_tune --ctrl adrc|lqr|mpc` (CMA-ES, same scenario and cost as the PID).
"""

from __future__ import annotations

import math

import numpy as np

DT = 0.01
AMAX = 981.0 * math.tan(math.radians(15.0))     # tilt cap cm/s^2 (xy_tune TILT_DEG)
INT_MAX = 300.0                                  # cm s, integral clamp (PROPOSED)
INT_BAND = 10.0                                  # cm: integrate only when |pos - des| < band (firmware PID EMin rule)


def _integrate(z, pos, des):
    e = pos - des
    return np.clip(z + DT * np.where(np.abs(e) < INT_BAND, e, 0.0), -INT_MAX, INT_MAX)


def nominal(plants) -> dict:
    """Mean plant at 100 Hz: k, tau s, drag 1/s, d ticks."""
    return {"k": float(np.mean([p.k for p in plants])), "tau": float(np.mean([p.tau for p in plants])),
            "drag": float(np.mean([p.drag for p in plants])),
            "d": int(round(np.mean([p.delay for p in plants]) * 0.02 / DT))}


def model_ad(m: dict) -> tuple[np.ndarray, np.ndarray]:
    """One 10 ms tick of [pos, vel, a] (xy_tune.simulate order: a, then v with the new a, then pos)."""
    al, k, dr = math.exp(-DT / m["tau"]), m["k"], m["drag"]
    a = np.array([[1.0, DT * (1 - dr * DT), DT * DT * k * al],
                  [0.0, 1 - dr * DT, DT * k * al],
                  [0.0, 0.0, al]])
    b = np.array([DT * DT * k * (1 - al), DT * k * (1 - al), 1 - al])
    return a, b


class Predictor:
    """Command history (d ticks) + lag-state estimate -> [pos, vel, a] when the command issued now starts acting."""

    def __init__(self, m: dict, b: int):
        self.m, self.d = m, m["d"]
        self.al = math.exp(-DT / m["tau"])
        self.a_mat, self.b_vec = model_ad(m)
        self.hist = np.zeros((max(self.d, 1), b))    # hist[0] = oldest (acts next), hist[-1] = newest
        self.a_est = np.zeros(b)
        phi = np.eye(3)
        gam = np.zeros((3, self.d))
        for j in range(self.d):                      # x_d = A^d x0 + sum_j A^(d-1-j) B u_j
            gam = self.a_mat @ gam
            gam[:, j] += self.b_vec
            phi = self.a_mat @ phi
        self.phi, self.gam = phi, gam

    def predict(self, pos, vel) -> np.ndarray:
        x0 = np.vstack([pos, vel, self.a_est])
        return self.phi @ x0 + (self.gam @ self.hist if self.d else 0.0)

    def push(self, u: np.ndarray) -> None:
        """After the command: the oldest command reaches the plant's lag this tick."""
        oldest = self.hist[0] if self.d else u
        self.a_est = self.al * self.a_est + (1 - self.al) * oldest
        if self.d:
            self.hist = np.roll(self.hist, -1, 0)
            self.hist[-1] = u


def _err_state(x, des, vff, z, m):
    """[e_pos, e_vel, a - a_ff, integral] with a_ff = drag vff / k (the accel that holds vff against drag)."""
    return np.vstack([x[0] - des, x[1] - vff, x[2] - m["drag"] * vff / m["k"], z])


def aug_ad(m: dict) -> tuple[np.ndarray, np.ndarray]:
    a, b = model_ad(m)
    aa = np.zeros((4, 4))
    aa[:3, :3] = a
    aa[3, 0], aa[3, 3] = DT, 1.0
    return aa, np.append(b, 0.0)


def lqr_gain(m: dict, q: tuple[float, float, float, float], r: float = 1.0) -> np.ndarray:
    from scipy.linalg import solve_discrete_are
    a, b = aug_ad(m)
    b = b.reshape(4, 1)
    p = solve_discrete_are(a, b, np.diag(q), np.array([[r]]))
    return (np.linalg.solve(r + b.T @ p @ b, b.T @ p @ a)).ravel()


class Lqr:
    def __init__(self, m: dict, kgain: np.ndarray, b: int):
        self.m, self.k, self.pred = m, np.asarray(kgain, float), Predictor(m, b)
        self.z = np.zeros(b)

    def step(self, des, pos, vel, vff=0.0):
        vff = np.broadcast_to(vff, np.shape(pos))
        self.z = _integrate(self.z, pos, des)
        x = _err_state(self.pred.predict(pos, vel), des, vff, self.z, self.m)
        u = np.clip(self.m["drag"] * vff / self.m["k"] - self.k @ x, -AMAX, AMAX)
        self.pred.push(u)
        return u


class Adrc:
    def __init__(self, m: dict, wc: float, wo: float, b0: float, b: int):
        self.wc, self.b0, self.d = wc, b0, m["d"]
        self.beta = (3 * wo, 3 * wo * wo, wo ** 3)
        self.z1, self.z2, self.z3 = np.zeros(b), np.zeros(b), np.zeros(b)
        self.hist = np.zeros((max(self.d, 1), b))
        self.init = False

    def step(self, des, pos, vel, vff=0.0):
        if not self.init:
            self.z1, self.init = np.array(pos, float), True
        u_del = self.hist[0] if self.d else 0.0
        e = self.z1 - pos
        b1, b2, b3 = self.beta
        self.z1 = self.z1 + DT * (self.z2 - b1 * e)
        self.z2 = self.z2 + DT * (self.z3 - b2 * e + self.b0 * u_del)
        self.z3 = self.z3 + DT * (-b3 * e)
        u0 = self.wc ** 2 * (des - self.z1) + 2 * self.wc * (vff - self.z2)
        u = np.clip((u0 - self.z3) / self.b0, -AMAX, AMAX)
        if self.d:
            self.hist = np.roll(self.hist, -1, 0)
            self.hist[-1] = u
        return u


def mpc_matrices(m: dict, q: tuple[float, float, float, float], rd: float, n: int = 10, mb: int = 10):
    """Condensed QP for U (n blocks, each held mb ticks) from the predicted error state x (4):
    J = sum_blocks e' Q e + r u^2 + rd du^2 (r = 1; e at block ends) -> H (n, n), F (n, 4), G (n,) on u_prev."""
    a, b = aug_ad(m)
    ab, bb = np.eye(4), np.zeros(4)
    for _ in range(mb):                               # one block: x+ = Ab x + Bb u
        bb = a @ bb + b
        ab = a @ ab
    sx = np.zeros((4 * n, 4))
    su = np.zeros((4 * n, n))
    pw = np.eye(4)
    for i in range(n):
        pw = ab @ pw
        sx[4 * i:4 * i + 4] = pw
        for j in range(i + 1):
            sx_blk = np.linalg.matrix_power(ab, i - j) @ bb
            su[4 * i:4 * i + 4, j] = sx_blk
    qq = np.kron(np.eye(n), np.diag(q))
    dmat = np.eye(n) - np.eye(n, k=-1)
    h = su.T @ qq @ su + np.eye(n) + rd * dmat.T @ dmat
    f = su.T @ qq @ sx
    e0 = np.zeros(n)
    e0[0] = 1.0
    g = -rd * e0                                       # du_0 = u_0 - u_prev: linear term -rd u_prev on u_0
    return h, f, g


class Mpc:
    def __init__(self, m: dict, q, rd: float, b: int, n: int = 10, mb: int = 10, iters: int = 30):
        self.m, self.pred, self.iters = m, Predictor(m, b), iters
        self.h, self.f, self.g = mpc_matrices(m, q, rd, n, mb)
        self.step_len = 1.0 / float(np.linalg.eigvalsh(self.h).max())
        self.u = np.zeros((n, b))
        self.z = np.zeros(b)
        self.u_prev = np.zeros(b)

    def step(self, des, pos, vel, vff=0.0):
        vff = np.broadcast_to(vff, np.shape(pos))
        self.z = _integrate(self.z, pos, des)
        x = _err_state(self.pred.predict(pos, vel), des, vff, self.z, self.m)
        uff = self.m["drag"] * vff / self.m["k"]
        lin = self.f @ x + np.outer(self.g, self.u_prev - uff)   # in deviation from u_ff
        u = y = self.u                                            # warm start: last plan (blocks >> 1 tick)
        t = 1.0
        for _ in range(self.iters):                               # FISTA (accelerated projected gradient)
            u_new = np.clip(y - self.step_len * (self.h @ y + lin), -AMAX - uff, AMAX - uff)
            t_new = 0.5 * (1.0 + math.sqrt(1.0 + 4.0 * t * t))
            y, u, t = u_new + (t - 1.0) / t_new * (u_new - u), u_new, t_new
        self.u = u
        out = np.clip(u[0] + uff, -AMAX, AMAX)
        self.u_prev = out
        self.pred.push(out)
        return out


# ---------------------------------------------------------------- tuning knobs (log10 bounds, PROPOSED)

KNOBS = {
    "adrc": (("wc", -0.7, 0.7), ("wo", 0.0, 1.3), ("b0", -0.5, 0.5)),          # rad/s, rad/s, x model k
    "lqr": (("q_pos", -2.0, 3.0), ("q_vel", -3.0, 2.0), ("q_int", -4.0, 1.0)),  # per 10 ms tick, r = 1
    "mpc": (("q_pos", -1.0, 3.0), ("q_vel", -2.0, 2.0), ("q_int", -3.0, 1.0), ("rd", -2.0, 2.0)),  # per block
}
X0 = {"adrc": (0.0, 0.7, 0.0), "lqr": (0.0, -1.0, -2.0), "mpc": (1.5, 0.0, -1.0, 0.0)}


def factory(kind: str, m: dict, logx) -> callable:
    """Controller factory b -> controller from log10 knob values."""
    v = [10.0 ** float(x) for x in logx]
    if kind == "adrc":
        return lambda b: Adrc(m, v[0], v[1], v[2] * m["k"], b)
    if kind == "lqr":
        kg = lqr_gain(m, (v[0], v[1], 0.0, v[2]))
        return lambda b: Lqr(m, kg, b)
    if kind == "mpc":
        return lambda b: Mpc(m, (v[0], v[1], 0.0, v[2]), v[3], b)
    raise ValueError(kind)
