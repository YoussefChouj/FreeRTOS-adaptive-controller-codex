"""SIL engine: batches of (controller, scenario, seed) rows, one firmware process per row, one vectorised plant.

Per 200 Hz tick: plant estimates -> firmware inputs (frames per plant.py), all firmware processes step, their motor
commands drive the plant for 5 ms, then the plant.run crash rule. A crashed row stops stepping its firmware.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from sim.sil import fw
from sim.sil.controllers import ControllerSpec
from sim.sil.plant import DT_C, Plant, plant_to_fw_xy
from sim.sil.scenarios import WARMUP, Scenario

TRIM = (0.0, 0.0)   # PROPOSED: g_att_trim_pitch/roll_deg (StabilizerTask.c:1133-1134, -0.90/-1.24) zeroed: the
                    # plant IMU has no mounting offset for them to cancel
LOG_OUT = ("corr_p", "corr_r", "corr_y", "corr_z", "unom_p", "unom_r", "unom_y", "unom_z", "th_p", "th_r", "th_y",
           "th_z", "tripped", "inj", "gx_des", "gy_des", "gz_des", "vid_p", "vid_r", "vid_y", "host_us")


@dataclass
class Case:
    ctrl: ControllerSpec
    scen: Scenario
    seed: int = 0
    dither: np.ndarray | None = None     # (N, 3) deg/s on gyroy/gyrox/gyroz Des, the SysID site


@dataclass
class Log:
    """Arrays over the case's ticks (warmup included). Positions in the firmware world frame, m."""
    case: Case
    t: np.ndarray            # scenario time, s (negative during warmup)
    p: np.ndarray            # true position in the navigation frame (N, 3), plant.Plant.p_nav
    hdg: np.ndarray          # heading estimate error psi_hat - psi, deg (N,)
    ref: np.ndarray          # reference (N, 3)
    ff: np.ndarray           # TrajFF active (N,)
    att: np.ndarray          # true roll, pitch, yaw deg (N, 3)
    gyro: np.ndarray         # gyro the firmware read, plant p q r deg/s (N, 3)
    mot: np.ndarray          # motor commands after the firmware clamp (N, 4)
    out: dict                # LOG_OUT firmware outputs, (N,) each
    applied: list            # per command: accepted by the firmware parser
    crash_k: int             # first crashed tick, -1 if none


def _metrics_job(jobs: list[tuple]) -> list[dict]:
    """Worker: (ControllerSpec, scenario spec string, seed) jobs -> metrics.compute of each."""
    from sim.sil import metrics, scenarios
    return [metrics.compute(lg) for lg in run([Case(c, scenarios.parse(s), seed) for c, s, seed in jobs])]


def run_metrics(jobs: list[tuple], workers: int = 4) -> list[dict]:
    """metrics for (ControllerSpec, scenario spec, seed) jobs, split over `workers` processes (each runs its own
    firmware processes). Jobs are dealt round-robin by length so the workers finish together."""
    from concurrent.futures import ProcessPoolExecutor
    from sim.sil import scenarios
    order = sorted(range(len(jobs)), key=lambda i: -scenarios.parse(jobs[i][1]).n_ticks())
    parts = [order[w::workers] for w in range(workers)]
    out: list[dict | None] = [None] * len(jobs)
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for part, res in zip(parts, ex.map(_metrics_job, [[jobs[i] for i in p] for p in parts])):
            for i, m in zip(part, res):
                out[i] = m
    return out


def run(cases: list[Case], batch: int = 48) -> list[Log]:
    order = sorted(range(len(cases)), key=lambda i: cases[i].scen.n_ticks())
    logs: list[Log | None] = [None] * len(cases)
    for s in range(0, len(order), batch):
        idx = order[s:s + batch]
        for i, lg in zip(idx, _run_batch([cases[i] for i in idx])):
            logs[i] = lg
    return logs


def _run_batch(cases: list[Case]) -> list[Log]:
    B = len(cases)
    refs = [c.scen.reference() for c in cases]
    n = np.array([len(r[0]) for r in refs])
    N = int(n.max())
    ref = np.zeros((B, N, 3))
    ff = np.zeros((B, N), bool)
    dith = np.zeros((B, N, 3))
    for i, ((p_ref, f), c) in enumerate(zip(refs, cases)):
        ref[i, :n[i]] = p_ref
        ref[i, n[i]:] = p_ref[-1]
        ff[i, :n[i]] = f
        if c.dither is not None:
            dith[i, :len(c.dither)] = c.dither[:N]
    rx, ry = ref[:, :, 0], ref[:, :, 1]
    ref_plant = np.stack([ry, -rx, ref[:, :, 2]], 2)          # fw_x = -plant_y, fw_y = plant_x
    plant = Plant([c.scen.plant_params(c.seed) for c in cases], ref_plant[:, 0])
    fws = [fw.Firmware(c.ctrl.variant) for c in cases]
    try:
        applied = [[f.cmd(*cmd) for cmd in c.ctrl.all_cmds()] for f, c in zip(fws, cases)]
        L = dict(p=np.zeros((B, N, 3), np.float32), hdg=np.zeros((B, N), np.float32), att=np.zeros((B, N, 3), np.float32),
                 gyro=np.zeros((B, N, 3), np.float32), mot=np.zeros((B, N, 4), np.float32),
                 out=np.zeros((B, N, len(LOG_OUT)), np.float32))
        oi = [fw.OUT_IDX[k] for k in LOG_OUT]
        crash_k = np.full(B, -1)
        rows = np.zeros((B, len(fw.IN)), np.float32)
        rows[:, fw.IN_IDX["trim_p"]], rows[:, fw.IN_IDX["trim_r"]] = TRIM
        M = plant.mot.copy()
        ix = fw.IN_IDX
        for k in range(N):
            t = k * DT_C
            o = plant.obs()
            rows[:, ix["pit"]], rows[:, ix["rol"]], rows[:, ix["yaw"]] = o["rpy"][:, 1], o["rpy"][:, 0], o["rpy"][:, 2]
            rows[:, ix["gx"]], rows[:, ix["gy"]], rows[:, ix["gz"]] = o["gyro"][:, 0], o["gyro"][:, 1], o["gyro"][:, 2]
            x, y = plant_to_fw_xy(o["pos"][:, 0], o["pos"][:, 1])
            vx, vy = plant_to_fw_xy(o["vel"][:, 0], o["vel"][:, 1])
            rows[:, ix["x"]], rows[:, ix["y"]], rows[:, ix["z"]] = 100 * x, 100 * y, o["pos"][:, 2]
            rows[:, ix["vx"]], rows[:, ix["vy"]], rows[:, ix["vz"]] = 100 * vx, 100 * vy, o["vel"][:, 2]
            rows[:, ix["tx"]], rows[:, ix["ty"]], rows[:, ix["tz"]] = 100 * ref[:, k, 0], 100 * ref[:, k, 1], ref[:, k, 2]
            rows[:, ix["ff"]] = ff[:, k]
            rows[:, ix["dp"]], rows[:, ix["dr"]], rows[:, ix["dy"]] = dith[:, k, 0], dith[:, k, 1], dith[:, k, 2]
            plant.alive &= k < n                                   # a finished row freezes; it did not crash
            active = np.flatnonzero(plant.alive)
            out = fw.step_all(fws, rows, active)
            M[active] = out[active, :4]
            plant.tick(M, t)
            bad = plant.crash_check(ref_plant[:, k])
            crash_k[bad & (crash_k < 0)] = k
            px, py = plant_to_fw_xy(plant.p_nav[:, 0], plant.p_nav[:, 1])
            L["p"][:, k] = np.stack([px, py, plant.p_nav[:, 2]], 1)
            L["hdg"][:, k] = np.rad2deg(plant.ehat[:, 2] - plant.e[:, 2])
            L["att"][:, k] = np.rad2deg(plant.e)
            L["gyro"][:, k] = o["gyro"]
            L["mot"][:, k] = M
            L["out"][active, k] = out[np.ix_(active, oi)]
    finally:
        for f in fws:
            f.close()
    logs = []
    for i, c in enumerate(cases):
        m = n[i]
        logs.append(Log(c, np.arange(m) * DT_C - WARMUP, L["p"][i, :m], L["hdg"][i, :m], ref[i, :m], ff[i, :m],
                        L["att"][i, :m],
                        L["gyro"][i, :m], L["mot"][i, :m], {k: L["out"][i, :m, j] for j, k in enumerate(LOG_OUT)},
                        applied[i], int(crash_k[i])))
    return logs
