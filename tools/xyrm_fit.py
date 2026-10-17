#!/usr/bin/env python3
"""Fit closed-loop xy bandwidth for each controller kind (0-3) on a figure-8 profile.

Model: double integrator + attitude lag + delay g_xyc_delay_ticks (12), nominal plant
from ground_station/analysis/tests/test_xy_ctrl_host.py:22 (k=0.674, tau=0.0674, drag=0.426).
For each kind 0-3: fit bw of xm' = bw*(Des - xm) to velocity FB (least squares).
"""
from __future__ import annotations

import math
import sys
from pathlib import Path
import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from ground_station.analysis import xy_ctrl as xc  # noqa: E402 (after sys.path insert)
from ground_station.analysis import xy_tune as xt  # noqa: E402 (after sys.path insert)

M = {"k": 0.674, "tau": 0.0674, "drag": 0.426, "d": 12}
DT = 0.01


def generate_figure8_profile(a_cm: float = 70.0, move_s: float = 20.0,
                             ramp_s: float = 2.0, laps: float = 2.0,
                             dt: float = DT) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Generates figure-8 pos_des, vff, and time array."""
    n = int(round(move_s / dt))
    t = np.arange(n) * dt
    path_s = move_s - ramp_s
    warp = np.zeros(n)
    for i in range(n):
        ti = t[i]
        if ti <= 0.0:
            warp[i] = 0.0
        elif ti >= move_s:
            warp[i] = path_s
        elif ti < ramp_s:
            warp[i] = 0.5 * ti - ramp_s / (2.0 * math.pi) * math.sin(math.pi * ti / ramp_s)
        elif ti > move_s - ramp_s:
            ui = move_s - ti
            warp[i] = path_s - (0.5 * ui - ramp_s / (2.0 * math.pi) * math.sin(math.pi * ui / ramp_s))
        else:
            warp[i] = 0.5 * ramp_s + (ti - ramp_s)

    om = 2.0 * math.pi * laps / path_s
    th = om * warp
    # Gerono figure-8 x offset
    pos_des = 0.5 * a_cm * np.sin(2.0 * th)
    vff = np.gradient(pos_des, dt)
    return t, pos_des, vff


def simulate_kind(kind: int, pos_des: np.ndarray, vff: np.ndarray,
                  dt: float = DT) -> tuple[np.ndarray, np.ndarray]:
    """Simulates closed loop for controller kind (0 PID, 1 ADRC, 2 LQR, 3 MPC).

    Returns (Des, FB) velocity in cm/s.
    """
    n = len(pos_des)
    k, tau, drag, delay = M["k"], M["tau"], M["drag"], M["d"]
    amax = xt.G_CMS2 * math.tan(math.radians(xt.TILT_DEG))
    al = math.exp(-dt / tau)

    lmax = delay + 1
    buf = np.zeros(lmax)
    a = 0.0
    v = 0.0
    pos = 0.0

    pos_pid = xt.FwPid(xt.LOCX_ROW, b=1)

    if kind == 0:  # PID
        vel_pid = xt.FwPid(xt.LOCXS_ROW, b=1)
        ctrl = None
    elif kind == 1:  # ADRC
        ctrl = xc.Adrc(M, 1.34060242, 19.36, 0.572712305, 1)
    elif kind == 2:  # LQR
        ctrl = xc.Lqr(M, [29.7682778, 10.0523956, 0.35933145, 2.2333094], 1)
    elif kind == 3:  # MPC
        ctrl = xc.Mpc(M, (1000.0, 0.0189, 0.0, 10.0), 0.0399208702, 1)
    else:
        raise ValueError(f"Unknown kind {kind}")

    des_list = []
    fb_list = []

    for i in range(n):
        # Position PID computes locxsPID.Des
        vd = np.clip(pos_pid.step(np.array([pos_des[i] - pos])), -xt.VXY_MAX, xt.VXY_MAX)[0] + vff[i]
        vel_fb = v * 100.0
        des_list.append(vd)
        fb_list.append(vel_fb)

        if kind == 0:
            acmd = vel_pid.step(np.array([vd - vel_fb]))[0]
        else:
            acmd = ctrl.step(np.array([pos_des[i]]), np.array([pos]),
                             np.array([vel_fb]), np.array([vff[i]]))[0]

        acc = np.clip(acmd, -amax, amax)
        buf[i % lmax] = acc / 100.0
        u_delayed = buf[(i - delay) % lmax]
        a = al * a + (1 - al) * u_delayed
        v = v + dt * (k * a - drag * v)
        pos = pos + v * 100.0 * dt

    return np.array(des_list), np.array(fb_list)


def fit_bw(des: np.ndarray, fb: np.ndarray, dt: float = DT) -> tuple[float, float]:
    """Fits bw of xm' = bw*(Des - xm) to FB using least squares via golden section search."""
    n = len(des)

    def sim_xm(bw: float) -> np.ndarray:
        xm = np.zeros(n)
        cur = 0.0
        for i in range(n):
            cur += dt * bw * (des[i] - cur)
            xm[i] = cur
        return xm

    def cost(bw: float) -> float:
        return float(np.sum((sim_xm(bw) - fb) ** 2))

    a, b = 0.1, 50.0
    invphi = (math.sqrt(5) - 1) / 2
    invphi2 = (3 - math.sqrt(5)) / 2
    c = a + invphi2 * (b - a)
    d = a + invphi * (b - a)
    fc = cost(c)
    fd = cost(d)
    for _ in range(60):
        if fc < fd:
            b = d
            d = c
            fd = fc
            c = a + invphi2 * (b - a)
            fc = cost(c)
        else:
            a = c
            c = d
            fc = fd
            d = a + invphi * (b - a)
            fd = cost(d)

    bw_fit = (a + b) / 2
    rms_fit = float(np.sqrt(np.mean((sim_xm(bw_fit) - fb) ** 2)))
    return bw_fit, rms_fit


def main() -> int:
    _, pos_des, vff = generate_figure8_profile()
    for kind in range(4):
        des, fb = simulate_kind(kind, pos_des, vff)
        bw, rms = fit_bw(des, fb)
        print(f"kind {kind} bw {bw:.4f} rms_fit {rms:.4f}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
