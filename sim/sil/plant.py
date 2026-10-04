"""SIL plant: the calibrated bench_v1 quadrotor of sim/bench/plant.py (6-DOF at 1 kHz, mixer-to-thrust curve, motor
lag, 15 ms command delay, gyro/accel filters, attitude filter, ToF/OF Kalman estimator with sensor delays), made
steppable one 200 Hz control tick at a time so the firmware step server can close the loop.

Every constant not defined here is imported from sim/bench/plant.py, whose docstring cites its sources (PREV mujoco
model, logged hover, calib_replay/calib_check). The integration, environment and estimator code follows plant.run
line for line; additions for WP-31 are marked "WP-31" and their numbers are PROPOSED (defaults in scenarios.py):
per-row extra command delay, cable-hung swinging payload, rigid offset payload, wind that steps on at a time,
thrust-gain drift (battery sag), motor loss at a time. Rows are independent: each draws its noise from its own seed,
so a row's result does not depend on which batch it runs in.

One plant.py number is replaced: the rate effectiveness per mixer unit comes from the sysid gains of
ground_station/research/sim/constants.py (ROLL_K, PITCH_K over MIXER_R_P; YAW_K over MIXER_YAW): 8.08, 9.06 and
1.13 deg/s^2 per U. plant.py has roll 8.0 (replay-calibrated, kept within 1 %), pitch 7.2 (inertia ratio only) and
yaw 7.55 (sim_coupled.py, Jz 0.0015 = Izz / 10); at 7.55 the firmware yaw loop (gyroz Kp 8) limit-cycles at about
200 deg/s in the SIL while the flights hold heading. Inertias are set so the mixer-to-rate gains equal these.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np

from ground_station.research.sim import constants as C

_spec = importlib.util.spec_from_file_location("bench_plant", Path(__file__).resolve().parents[1] / "bench" / "plant.py")
bp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bp)

G, DT, DT_C, SUB = bp.G, bp.DT, bp.DT_C, bp.SUB
B_DPS = np.rad2deg([C.ROLL_K / C.MIXER_R_P, C.PITCH_K / C.MIXER_R_P, C.YAW_K / C.MIXER_YAW])   # deg/s^2 per U
J_PHYS = np.array([C.Ixx, C.Iyy, C.Izz])
J_SIL = np.array([np.rad2deg(4 * bp.ARM * bp.DTDX_H) / B_DPS[0], np.rad2deg(4 * bp.ARM * bp.DTDX_H) / B_DPS[1], C.Izz])
J_SCALE = J_SIL / J_PHYS                        # effective / physical inertia (plant.py's J_RP_SCALE idea, per axis)
KAPPA = np.deg2rad(B_DPS[2]) * J_SIL[2] / (4 * bp.DTDX_H)   # yaw torque per N of pair thrust difference
NOISE_BLOCK = 200                     # control ticks of noise drawn per row at a time (1 s)
CABLE_W, CABLE_ZETA = 150.0, 0.7      # WP-31 PROPOSED: axial mode of the payload cable (stiff, damped spring)
PAYLOAD_DRAG = 0.01                   # WP-31 PROPOSED: N/(m/s) air drag on the hanging payload
# Firmware world frame (fw_x, fw_y) vs plant frame: fw_x = -plant_y, fw_y = plant_x (sim/sil/README in run.py doc).


GVEC = np.array([0.0, 0.0, G])
_P1, _P2 = [1, 2, 0], [2, 0, 1]


def _cross(a, b):
    return a[:, _P1] * b[:, _P2] - a[:, _P2] * b[:, _P1]


def _rotm(e):
    """plant.rot as one (B, 3, 3) body->world matrix (columns b1 b2 b3), plus the trig euler_rates reuses."""
    sf, cf = np.sin(e[:, 0]), np.cos(e[:, 0])
    st, ct = np.sin(e[:, 1]), np.cos(e[:, 1])
    sp, cp = np.sin(e[:, 2]), np.cos(e[:, 2])
    R = np.empty((len(e), 3, 3))
    R[:, 0, 0], R[:, 1, 0], R[:, 2, 0] = cp * ct, sp * ct, -st
    R[:, 0, 1], R[:, 1, 1], R[:, 2, 1] = cp * st * sf - sp * cf, sp * st * sf + cp * cf, ct * sf
    R[:, 0, 2], R[:, 1, 2], R[:, 2, 2] = cp * st * cf + sp * sf, sp * st * cf - cp * sf, ct * cf
    return R, sf, cf, ct, st / ct


def fw_to_plant_xy(x_fw, y_fw):
    return np.asarray(y_fw), -np.asarray(x_fw)


def plant_to_fw_xy(x_p, y_p):
    return -np.asarray(y_p), np.asarray(x_p)


class Plant:
    """B independent rows. `rows` = list of parameter dicts (scenarios.plant_params); state arrays are (B, ...)."""

    def __init__(self, rows: list[dict], p0: np.ndarray):
        B = self.B = len(rows)
        q = {k: np.array([r[k] for r in rows]) for k in rows[0] if k != "seed"}
        self.q = q
        self.rngs = [np.random.default_rng(r["seed"]) for r in rows]
        self.k = 0
        m_hang = q["pend_m"]
        self.m, self.J = q["mass"].astype(float), q["J"].astype(float)
        self.p, self.v = p0.astype(float).copy(), np.zeros((B, 3))
        self.e, self.w = np.zeros((B, 3)), np.zeros((B, 3))
        x_h = (-bp.A1 + np.sqrt(bp.A1 ** 2 + 4 * bp.A2 * (self.m + m_hang) * G / 4 / (q["V0"] / bp.V_NOM) ** 2)) / (2 * bp.A2)
        self.mot = np.repeat((bp.PWM_MIN + x_h)[:, None], 4, 1)
        self.delay = bp.DELAY_TICKS + q["extra_delay"].astype(int)
        self.ring = np.repeat(self.mot[:, None, :], int(self.delay.max()) + 1, 1)
        f0 = np.zeros((B, 3))
        f0[:, 2] = G
        self.gyro_f = bp.IIR(bp.BG_B, bp.BG_A, (B, 3))
        self.acc_f = bp.IIR(bp.BA_B, bp.BA_A, (B, 3), f0 + q["acc_bias"])
        self.ehat = self.e.copy()
        self.xkf = np.zeros((B, 3, 3))
        self.xkf[:, :, 0] = self.p
        self.zhist = [self.p[:, 2].copy() for _ in range(bp.TOF_DELAY + 1)]
        self.vhist = [self.v[:, :2].copy() for _ in range(17)]
        self.tz_imb = -q["u_imb"] * np.deg2rad(B_DPS[2]) * J_SIL[2]   # plant.run: u_imb yaw U of imbalance
        self.turb = np.zeros((B, 3))
        self.alive = np.ones(B, bool)
        self.g_meas = np.zeros((B, 3))
        self.a_meas = f0 + q["acc_bias"]
        # WP-31 swinging payload: point mass on a cable from a hook below the CoG, hanging at rest, then given the
        # horizontal speed pend_kick (m/s, plant +x) at pend_kick_t
        self.has_pend = m_hang > 0
        self.any_pend = bool(self.has_pend.any())
        self.mp = np.where(self.has_pend, m_hang, 1.0)
        self.pp = self.p + q["hook"]
        self.pp[:, 2] -= q["pend_L"] + G / CABLE_W ** 2
        self.pv = np.zeros((B, 3))
        self.cable_f = np.zeros((B, 3))
        self.p_nav = self.p.copy()      # true position in the navigation frame (see tick)
        self._noise = None

    # ---- noise: per-row generators, one block of NOISE_BLOCK ticks at a time ----
    def _draw(self):
        n = NOISE_BLOCK
        blk = [dict(g=r.standard_normal((n, SUB, 3)), a=r.standard_normal((n, SUB, 3)), of=r.standard_normal((n, 2)),
                    tof=r.standard_normal(n), turb=r.standard_normal((n, 3))) for r in self.rngs]
        self._noise = {k: np.stack([b[k] for b in blk], 0) for k in blk[0]}

    def wind(self, t: float) -> np.ndarray:
        q = self.q
        w = q["wind"] * (t >= q["wind_t0"])[:, None] + self.turb
        for j in range(q["gust_t"].shape[1]):
            tau = (t - q["gust_t"][:, j]) / q["gust_dur"][:, j]
            on = (tau >= 0) & (tau <= 1)
            w = w + (on * 0.5 * (1 - np.cos(2 * np.pi * np.clip(tau, 0, 1))))[:, None] * q["gust_vec"][:, j]
        return w

    def obs(self) -> dict:
        """Estimates the firmware reads (plant frame): attitude deg, gyro deg/s, KF position m and velocity m/s."""
        return dict(rpy=np.rad2deg(self.ehat), gyro=self.g_meas.copy(), pos=self.xkf[:, :, 0].copy(),
                    vel=self.xkf[:, :, 1].copy())

    def tick(self, M: np.ndarray, t: float) -> None:
        """Apply the motor commands of this control tick and advance 5 ms (plant.run body, one k)."""
        q, B, k = self.q, self.B, self.k
        if k % NOISE_BLOCK == 0:
            self._draw()
        nb = k % NOISE_BLOCK
        ns = q["noise_scale"]
        R = self.ring.shape[1]
        self.ring[:, k % R] = np.clip(np.nan_to_num(M, nan=bp.PWM_MIN), bp.PWM_MIN, bp.PWM_MAX)
        Md = self.ring[np.arange(B), (k - self.delay) % R]
        # ---- per-tick environment ----
        V = q["V0"] - q["vsag"] * t
        drift = 1.0 - q["thrust_drift"] * np.clip((t - q["drift_t0"]) / q["drift_T"], 0.0, 1.0)   # WP-31 battery sag
        kick = (t <= q["pend_kick_t"]) & (q["pend_kick_t"] < t + DT_C)
        self.pv[:, 0] += q["pend_kick"] * kick
        gain = q["mgain"] * ((V / bp.V_NOM) ** 2 * drift)[:, None]
        lost = t >= q["mloss_t"]
        gain[np.arange(B), q["mloss_idx"]] *= np.where(lost, q["mloss_eff"], 1.0)
        ge = 1.0 / (1.0 - np.minimum((bp.R_PROP / (4 * np.maximum(self.p[:, 2], 0.03))) ** 2, 0.25))
        gain *= ge[:, None]
        self.turb += DT_C / 1.0 * (-self.turb) + q["dryden_sigma"][:, None] * np.sqrt(2 * DT_C / 1.0) * self._noise["turb"][:, nb]
        wind = self.wind(t)
        p, v, e, w, m, J, alive = self.p, self.v, self.e, self.w, self.m, self.J, self.alive
        hook_b, L = q["hook"], q["pend_L"]
        kc, cc = self.mp * CABLE_W ** 2, 2 * CABLE_ZETA * self.mp * CABLE_W
        # ---- 1 kHz plant + IMU (plant.run; rot / euler_rates / cross inlined for speed) ----
        fc = np.zeros((B, 3))
        tau = np.empty((B, 3))
        live = alive[:, None]
        for s in range(SUB):
            self.mot += DT / bp.TAU_M * (Md - self.mot)
            x = self.mot - bp.PWM_MIN
            T = (bp.A1 * x + bp.A2 * x * x) * gain
            Ts = T.sum(1)
            tau[:, 0] = bp.ARM * (T[:, 1] + T[:, 2] - T[:, 0] - T[:, 3]) - q["cog"][:, 1] * Ts
            tau[:, 1] = bp.ARM * (T[:, 1] + T[:, 3] - T[:, 0] - T[:, 2]) + q["cog"][:, 0] * Ts
            tau[:, 2] = KAPPA * (T[:, 2] + T[:, 3] - T[:, 0] - T[:, 1]) + self.tz_imb
            Rm, sf, cf, ct, tt = _rotm(e)                              # columns b1 b2 b3 = plant.rot(e)
            if self.any_pend:
                # WP-31 cable: tension only, at the hook (body point hook_b)
                hk = p + np.einsum("bij,bj->bi", Rm, hook_b)
                vh = v + np.einsum("bij,bj->bi", Rm, _cross(w, hook_b))
                d = self.pp - hk
                ln = np.maximum(np.sqrt((d * d).sum(1)), 1e-6)
                u = d / ln[:, None]
                ten = np.maximum(kc * (ln - L) + cc * ((self.pv - vh) * u).sum(1), 0.0) * self.has_pend
                fc = ten[:, None] * u                                 # force on the drone, world frame
                fp = -fc - PAYLOAD_DRAG * (self.pv - wind)
                fp[:, 2] -= self.mp * G
                self.pv += DT * fp / self.mp[:, None] * self.has_pend[:, None]
                self.pp += DT * self.pv
                tau += _cross(hook_b, np.einsum("bij,bi->bj", Rm, fc))
            fw = (Ts[:, None] * Rm[:, :, 2] - bp.DRAG_LIN * (v - wind) + fc) / m[:, None]
            v += DT * (fw - GVEC) * live
            p += DT * v
            wd = (tau - bp.DRAG_ROT * w - _cross(w, J * w)) / J
            w += DT * wd * live
            a = w[:, 1] * sf + w[:, 2] * cf                            # plant.euler_rates(e, w)
            e[:, 0] += DT * (w[:, 0] + a * tt)
            e[:, 1] += DT * (w[:, 1] * cf - w[:, 2] * sf)
            e[:, 2] += DT * (a / ct)
            fb = np.einsum("bij,bi->bj", Rm, fw)
            g_raw = np.rad2deg(w) + q["gyro_bias"] + bp.SIG_GYRO * ns[:, None] * self._noise["g"][:, nb, s]
            a_raw = fb + q["acc_bias"] + bp.SIG_ACC * ns[:, None] * self._noise["a"][:, nb, s]
            self.g_meas = self.gyro_f(g_raw)
            self.a_meas = self.acc_f(a_raw)
        self.cable_f = fc
        # ---- attitude estimate (plant.run Mahony-lite) ----
        a_meas, ehat, xkf = self.a_meas, self.ehat, self.xkf
        gr = np.deg2rad(self.g_meas)
        ea = np.stack([np.arctan2(a_meas[:, 1], a_meas[:, 2]),
                       np.arctan2(-a_meas[:, 0], np.hypot(a_meas[:, 1], a_meas[:, 2]))], 1)
        ed = bp.euler_rates(ehat, gr)
        cthat, cfhat = np.cos(ehat[:, 1]), np.cos(ehat[:, 0])
        ehat[:, :2] += DT_C * (ed[:, :2] + bp.KP_MAHONY * (ea - ehat[:, :2]))
        ehat[:, 2] += DT_C * ed[:, 2]
        # ---- position/velocity KFs (plant.run, steady-state gains) ----
        h1, h2, h3 = bp.rot(ehat)
        aw = h1 * a_meas[:, :1] + h2 * a_meas[:, 1:2] + h3 * a_meas[:, 2:3]
        aw[:, 2] -= G
        for ax in range(3):
            xa = xkf[:, ax]
            uu = aw[:, ax] - xa[:, 2]
            xa[:, 0] += DT_C * xa[:, 1] + 0.5 * DT_C ** 2 * uu
            xa[:, 1] += DT_C * uu
        self.zhist.append(p[:, 2].copy())
        self.zhist.pop(0)
        self.vhist.append(v[:, :2].copy())
        self.vhist.pop(0)
        if k % bp.TOF_EVERY == 0:
            ct, cf = np.cos(e[:, 1]), np.cos(e[:, 0])
            rng_m = self.zhist[0] / (ct * cf) + bp.SIG_TOF * ns * self._noise["tof"][:, nb]
            xkf[:, 2] += (rng_m * cthat * cfhat - xkf[:, 2, 0])[:, None] * bp.K_TOF
        if k % bp.OF_EVERY == 0:
            vd = np.stack(self.vhist, 0)[-1 - q["of_delay"], np.arange(B)]
            # WP-31: OF measures body-frame velocity and the firmware rotates it by its yaw estimate
            # (StabilizerTask.c:790-791), so the fused velocity is in the estimated frame: Rz(psi_hat - psi) v.
            # plant.run feeds world velocity, which leaves the estimator and the controller in different frames
            # once the heading estimate drifts.
            dpsi = ehat[:, 2] - e[:, 2]
            c, s_ = np.cos(dpsi), np.sin(dpsi)
            vd = np.stack([c * vd[:, 0] - s_ * vd[:, 1], s_ * vd[:, 0] + c * vd[:, 1]], 1)
            vm = vd * (1 + q["of_scale"])[:, None] + bp.SIG_OF * ns[:, None] * self._noise["of"][:, nb]
            for ax in range(2):
                xkf[:, ax] += (vm[:, ax] - xkf[:, ax, 1])[:, None] * bp.K_OF
        # WP-31 navigation-frame truth: the true velocity integrated in the frame the firmware flies (its heading
        # estimate), so heading drift (no magnetometer) is not scored as tracking error.
        dpsi = ehat[:, 2] - e[:, 2]
        c, s_ = np.cos(dpsi), np.sin(dpsi)
        self.p_nav[:, 0] += DT_C * (c * v[:, 0] - s_ * v[:, 1])
        self.p_nav[:, 1] += DT_C * (s_ * v[:, 0] + c * v[:, 1])
        self.p_nav[:, 2] = p[:, 2]
        self.k += 1

    def crash_check(self, ref_p: np.ndarray, div_err: float = 2.0) -> np.ndarray:
        """plant.run divergence rule: position error > div_err m (navigation frame), tilt > 60 deg, ground contact,
        non-finite."""
        err = np.linalg.norm(self.p_nav - ref_p, axis=1)
        bad = self.alive & ((err > div_err) | (np.abs(self.e[:, :2]).max(1) > np.deg2rad(60)) | (self.p[:, 2] < 0.01)
                            | ~np.isfinite(err))
        self.alive &= ~bad
        self.v[~self.alive] = 0
        self.w[~self.alive] = 0
        return bad
