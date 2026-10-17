import math
import numpy as np
from scipy.linalg import expm, solve_discrete_are

DT_ATT = 0.005 # 200 Hz
DT_Z = 0.010   # 100 Hz
ATT_NMAX = 10

def model_ad(m: dict) -> tuple[np.ndarray, np.ndarray]:
    a, b_gain, tau = m["a"], m["b"], m["tau"]
    dt = DT_Z if m["axis"] == "z" else DT_ATT
    A = np.array([[0, 1, 0],
                  [0, -a, b_gain],
                  [0, 0, -1.0/tau]])
    B = np.array([[0],
                  [0],
                  [1.0/tau]])
    n = A.shape[0]
    sys = np.zeros((n+1, n+1))
    sys[:n, :n] = A * dt
    sys[:n, n:] = B * dt
    sys_d = expm(sys)
    Ad = sys_d[:n, :n]
    Bd = sys_d[:n, n:]
    return Ad, Bd

def _integrate(z: np.ndarray, pos: np.ndarray, des: np.ndarray, dt: float, int_band: float, int_max: float) -> np.ndarray:
    e = pos - des
    mask = np.abs(e) < int_band
    z_new = z.copy()
    z_new[mask] = np.clip(z[mask] + dt * e[mask], -int_max, int_max)
    return z_new

class Predictor:
    def __init__(self, m: dict, b: int):
        self.d = int(m["d"])
        ad, bd = model_ad(m)
        self.n = 3
        self.phi = np.linalg.matrix_power(ad, self.d)
        self.gam = np.zeros((self.n, max(self.d, 1)))
        if self.d > 0:
            for j in range(self.d):
                self.gam[:, j] = (np.linalg.matrix_power(ad, self.d - 1 - j) @ bd).ravel()
        self.hist = np.zeros((max(self.d, 1), b))

    def predict(self, pos, vel, m_est):
        x0 = np.vstack([pos, vel, m_est])
        if self.d == 0:
            return x0
        return self.phi @ x0 + self.gam @ self.hist

    def push(self, u):
        if self.d > 0:
            self.hist = np.roll(self.hist, -1, axis=0)
            self.hist[-1] = u

def _err_state(x, des, vff, z, m):
    return np.vstack([x[0] - des, x[1] - vff, x[2], z])

def aug_ad(m: dict) -> tuple[np.ndarray, np.ndarray]:
    a, b = model_ad(m)
    dt = DT_Z if m["axis"] == "z" else DT_ATT
    aa = np.zeros((4, 4))
    aa[:3, :3] = a
    aa[3, 0], aa[3, 3] = dt, 1.0
    return aa, np.append(b, 0.0)

def lqr_gain(m: dict, q: tuple[float, float, float, float], r: float = 1.0) -> np.ndarray:
    a, b = aug_ad(m)
    b = b.reshape(4, 1)
    p = solve_discrete_are(a, b, np.diag(q), np.array([[r]]))
    return (np.linalg.solve(r + b.T @ p @ b, b.T @ p @ a)).ravel()

class Lqr:
    def __init__(self, m: dict, kgain: np.ndarray, b: int):
        self.m, self.k, self.pred = m, np.asarray(kgain, float), Predictor(m, b)
        self.z = np.zeros(b)
        self.dt = DT_Z if m["axis"] == "z" else DT_ATT
        self.int_band = m.get("int_band", 0.5)
        self.int_max = m.get("int_max", 50.0)
        self.m_est = np.zeros(b)

    def step(self, des, pos, vel, vff=0.0):
        vff = np.broadcast_to(vff, np.shape(pos))
        if self.m["axis"] == "yaw":
            pos = (pos + np.pi) % (2 * np.pi) - np.pi
            des = (des + np.pi) % (2 * np.pi) - np.pi
            pos = des + ((pos - des + np.pi) % (2 * np.pi) - np.pi)

        self.z = _integrate(self.z, pos, des, self.dt, self.int_band, self.int_max)
        x = _err_state(self.pred.predict(pos, vel, self.m_est), des, vff, self.z, self.m)
        u = np.clip(-self.k @ x, self.m["u_min"], self.m["u_max"])
        out = u[0] if u.ndim == 1 else u
        self.pred.push(out)
        u_del = self.pred.hist[0] if self.pred.d else out
        self.m_est = self.m_est + (u_del - self.m_est) * (self.dt / self.m["tau"])
        return out

class Adrc:
    def __init__(self, m: dict, wc: float, wo: float, b0: float, b: int):
        self.m, self.wc, self.b0, self.d = m, wc, b0, int(m["d"])
        self.beta = (2 * wo, wo * wo)
        self.z1, self.z2 = np.zeros(b), np.zeros(b)
        self.hist = np.zeros((max(self.d, 1), b))
        self.init = False
        self.dt = DT_Z if m["axis"] == "z" else DT_ATT

    def step(self, des, pos, vel, vff=0.0):
        y = pos
        w = vel
        if self.m["axis"] == "yaw":
            y = (y + np.pi) % (2 * np.pi) - np.pi
            des = (des + np.pi) % (2 * np.pi) - np.pi
            y = des + ((y - des + np.pi) % (2 * np.pi) - np.pi)

        if not self.init:
            self.z1, self.init = np.array(w, float), True

        u_del = self.hist[0] if self.d else 0.0
        e = self.z1 - w
        b1, b2 = self.beta

        self.z1 = self.z1 + self.dt * (self.z2 - b1 * e + self.b0 * u_del)
        self.z2 = self.z2 + self.dt * (-b2 * e)

        u0 = self.wc ** 2 * (des - y) - 2 * self.wc * w
        u = np.clip((u0 - self.z2) / self.b0, self.m["u_min"], self.m["u_max"])
        out = u[0] if u.ndim == 1 else u
        if self.d:
            self.hist = np.roll(self.hist, -1, axis=0)
            self.hist[-1] = out
        return out

def mpc_matrices(m: dict, q: tuple[float, float, float, float], r: float, n: int = 8):
    a, b = aug_ad(m)
    sx = np.zeros((4 * n, 4))
    su = np.zeros((4 * n, n))
    pw = np.eye(4)
    for i in range(n):
        pw = a @ pw
        sx[4 * i:4 * i + 4] = pw
        for j in range(i + 1):
            sx_blk = np.linalg.matrix_power(a, i - j) @ b
            su[4 * i:4 * i + 4, j] = sx_blk
    qq = np.kron(np.eye(n), np.diag(q))
    h = su.T @ qq @ su + r * np.eye(n)
    f = su.T @ qq @ sx
    return h, f

class Mpc:
    def __init__(self, m: dict, q, r: float, b: int, n: int = 8, iters: int = 15):
        self.m, self.pred, self.iters, self.n = m, Predictor(m, b), iters, n
        self.h, self.f = mpc_matrices(m, q, r, n)
        self.step_len = 1.0 / float(np.linalg.eigvalsh(self.h).max())
        self.u = np.zeros((n, b))
        self.z = np.zeros(b)
        self.dt = DT_Z if m["axis"] == "z" else DT_ATT
        self.int_band = m.get("int_band", 0.5)
        self.int_max = m.get("int_max", 50.0)
        self.m_est = np.zeros(b)

    def step(self, des, pos, vel, vff=0.0):
        vff = np.broadcast_to(vff, np.shape(pos))
        if self.m["axis"] == "yaw":
            pos = (pos + np.pi) % (2 * np.pi) - np.pi
            des = (des + np.pi) % (2 * np.pi) - np.pi
            pos = des + ((pos - des + np.pi) % (2 * np.pi) - np.pi)

        self.z = _integrate(self.z, pos, des, self.dt, self.int_band, self.int_max)
        x = _err_state(self.pred.predict(pos, vel, self.m_est), des, vff, self.z, self.m)
        lin = self.f @ x
        u = y = self.u
        t = 1.0
        for _ in range(self.iters):
            u_new = np.clip(y - self.step_len * (self.h @ y + lin), self.m["u_min"], self.m["u_max"])
            t_new = 0.5 * (1.0 + math.sqrt(1.0 + 4.0 * t * t))
            y, u, t = u_new + (t - 1.0) / t_new * (u_new - u), u_new, t_new

        self.u = np.roll(u, -1, axis=0)
        self.u[-1] = u[-1]

        out = u[0]
        out = out[0] if out.ndim == 1 else out
        self.pred.push(out)
        u_del = self.pred.hist[0] if self.pred.d else out
        self.m_est = self.m_est + (u_del - self.m_est) * (self.dt / self.m["tau"])
        return out

KNOBS = {
    "adrc": (("wc", -0.7, 1.5), ("wo", 0.0, 1.8), ("b0", -0.5, 0.5)),
    "lqr": (("q_int", -4.0, 1.0), ("q_pos", -2.0, 3.0), ("q_vel", -3.0, 2.0), ("q_m", -3.0, 2.0)),
    "mpc": (("q_int", -3.0, 1.0), ("q_pos", -1.0, 3.0), ("q_vel", -2.0, 2.0), ("q_m", -3.0, 2.0), ("r", -2.0, 2.0)),
}

X0 = {"adrc": (1.0, 1.3, 0.0), "lqr": (-2.0, 0.0, -1.0, -1.0), "mpc": (-1.0, 1.5, 0.0, -1.0, 0.0)}

# ground_station/autotune / sim defaults (PROPOSED)
MODELS = {
    "roll":  {"axis": "roll",  "a": 15.0, "b": 100.0, "tau": 0.025, "d": 3, "u_min": -400.0, "u_max": 400.0},
    "pitch": {"axis": "pitch", "a": 15.0, "b": 100.0, "tau": 0.025, "d": 3, "u_min": -400.0, "u_max": 400.0},
    "yaw":   {"axis": "yaw",   "a": 10.0, "b": 50.0,  "tau": 0.030, "d": 4, "u_min": -300.0, "u_max": 300.0},
    "z":     {"axis": "z",     "a": 2.0,  "b": 15.0,  "tau": 0.050, "d": 4, "u_min": -1000.0, "u_max": 1000.0}
}

def _c(v) -> str:
    s = f"{float(v):.9g}"
    return s + ("f" if any(ch in s for ch in ".e") else ".0f")

def _c_arr(name: str, a: np.ndarray) -> str:
    a = np.atleast_2d(np.asarray(a, float))
    rows = ",\n".join("\t{" + ", ".join(_c(v) for v in r) + "}" for r in a)
    return f"static const float {name}[{a.shape[0]}][{a.shape[1]}] = {{\n{rows}\n}};"

def fw_header(source: str = "") -> str:
    lines = [
        "/* GENERATED by `python -m ground_station.analysis.att_ctrl` - do not edit. */",
        f"/* source: {source} */" if source else "",
        "#ifndef ATT_CTRL_GAINS_H", "#define ATT_CTRL_GAINS_H", ""
    ]
    for axis in ["roll", "pitch", "yaw", "z"]:
        m = MODELS[axis]
        lines.append(f"/* {axis} */")
        pred = Predictor(m, 1)
        lq_knobs = [10.0 ** float(x) for x in X0["lqr"]]
        mp_knobs = [10.0 ** float(x) for x in X0["mpc"]]

        lines.append(f"#define ATTC_{axis.upper()}_D {int(m['d'])}")
        lines.append(_c_arr(f"ATTC_{axis.upper()}_PHI", pred.phi))
        lines.append(_c_arr(f"ATTC_{axis.upper()}_GAM", pred.gam))

        h, f = mpc_matrices(m, (mp_knobs[0], mp_knobs[1], mp_knobs[2], mp_knobs[3]), mp_knobs[4], ATT_NMAX)
        lines.append(_c_arr(f"ATTC_{axis.upper()}_MPC_H", h))
        lines.append(_c_arr(f"ATTC_{axis.upper()}_MPC_F", f))

        lq_k = lqr_gain(m, (lq_knobs[0], lq_knobs[1], lq_knobs[2], lq_knobs[3]))
        lines.append("static const float ATTC_" + axis.upper() + "_LQR_K[4] = {" + ", ".join(_c(v) for v in lq_k) + "};")

    lines.append("#endif\n")
    return "\n".join(lines)

if __name__ == "__main__":
    print(fw_header())
