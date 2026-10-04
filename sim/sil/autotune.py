"""pid_autotune: ground_station/autotune run on the SIL instead of a flight log.

Per axis (roll, pitch): the SIL hovers with the firmware PID while the SysID dither of the autotune campaigns
(excitation.ID_EXCITE: multisine 0.5-15 Hz, 60 deg/s, 30 s) is added to the rate setpoint at the firmware's SysID
site; frf.plant_frf (IV estimate, the dither as instrument) and frf.fit_plant give the rate plant, then
design.design_rate and design.design_angle propose the rate and angle rows. The result is a CMD 0x01 list
(idx = axis * 3 + gain, TASK/send_data.c:1522-1541) and the identification summary, cached per seed in build/.
"""
from __future__ import annotations

import json
import math
from dataclasses import replace

import numpy as np

from ground_station.autotune import design, excitation as ex, frf
from sim.sil import build
from sim.sil.controllers import CMD_PID, ControllerSpec
from sim.sil.plant import DT_C
from sim.sil.scenarios import WARMUP, Scenario, TRAJS, Traj
from sim.sil import engine

AXES = {"roll": ("gyroxPID", "rollPID", 1, 3), "pitch": ("gyroyPID", "pitchPID", 0, 4)}   # dither col, 0x01 axis


def identify(seed: int, rows: dict, cmds: tuple) -> dict:
    """One autotune round: fly the dither with `cmds` applied (rows = the PID rows they produce), design from it."""
    exc = ex.ID_EXCITE
    T = ex.step_s(exc["duration_s"]) + 2.0
    scen = Scenario(Traj("hover_id", "hover for identification", T, TRAJS["hover"].path))
    cases = []
    for axis, (_rate, _ang, col, _) in AXES.items():
        n = scen.n_ticks()
        t = np.arange(n) * DT_C - WARMUP - 1.0
        d = np.zeros((n, 3))
        d[:, col] = ex.dither(t, exc["signal"], exc["f0"], exc["f1"], exc["amp"], exc["duration_s"])
        cases.append(engine.Case(ControllerSpec("pid", "identification", cmds=cmds), scen, seed, d))
    out = {}
    for (axis, (rate, ang, col, fw_axis)), lg, case in zip(AXES.items(), engine.run(cases), cases):
        rel = lg.t - 1.0                                                  # dither start command at t = 1 s
        on = (rel >= ex.RAMP_T_S) & (rel < ex.RAMP_T_S + exc["duration_s"])   # sysid_state RUNNING
        des = lg.out["gx_des" if axis == "roll" else "gy_des"][on]
        x = lg.gyro[on, 0] if axis == "roll" else -lg.gyro[on, 1]       # gyroxPID.FB = p, gyroyPID.FB = -q
        run = frf.IdRun(1.0 / DT_C, case.dither[:len(lg.t), col][on], des, x, 0.0, "sil")
        f = frf.plant_frf([run], rows[rate], (exc["f0"], exc["f1"]))
        fit = frf.fit_plant(f)
        r = design.design_rate(fit.plant, rows[rate])
        a = design.design_angle(fit.plant, r.proposed or rows[rate], rows[ang]) if r.proposed else None
        out[axis] = dict(fit=dict(k=fit.plant.k, tau_s=fit.plant.tau_s, delay_s=fit.plant.delay_s,
                                  rel_residual=fit.rel_residual, coverage=f.coverage, problem=frf.quality_problem(f, fit)),
                         rate=r.to_dict(), angle=a.to_dict() if a else None, crashed=lg.crash_k >= 0,
                         fw_axis=fw_axis, ang_axis=0 if axis == "pitch" else 1)
    return out


MEMBER_BY_AXIS = {0: "pitchPID", 1: "rollPID", 3: "gyroxPID", 4: "gyroyPID"}   # CMD 0x01 axis -> pid.c member
ROUNDS = 3    # PROPOSED: the WP-25 tool moves each gain at most +-30 % per round, as one flight per round would


def _apply(ident: dict, rows: dict, gains: dict) -> bool:
    """Take this round's proposals into rows/gains ({0x01 axis: (Kp, Ki, Kd)}); True if anything changed."""
    changed = False
    for r in ident.values():
        for key, fw_axis in (("rate", r["fw_axis"]), ("angle", r["ang_axis"])):
            prop = (r.get(key) or {}).get("proposed")
            if not prop or not all(v is not None and math.isfinite(v) and 0.0 <= v <= 200.0 for v in prop.values()):
                continue
            new = (prop["Kp"], prop["Ki"], prop["Kd"])
            member = MEMBER_BY_AXIS[fw_axis]
            if new != (rows[member].kp, rows[member].ki, rows[member].kd):
                rows[member] = replace(rows[member], kp=new[0], ki=new[1], kd=new[2])
                gains[fw_axis] = new
                changed = True
    return changed


def autotune(seed: int = 0) -> tuple[tuple[tuple[int, int, float], ...], list]:
    """(CMD 0x01 list, per-round identification summaries), cached per seed and source digest."""
    path = build.BUILD / f"autotune_seed{seed}_{build.sim_digest()}.json"
    if path.exists():
        saved = json.loads(path.read_text())
        return tuple(tuple(c) for c in saved["cmds"]), saved["rounds"]
    rows, gains, rounds, cmds = design.read_pid_rows(), {}, [], ()
    for _ in range(ROUNDS):
        ident = identify(seed, rows, cmds)
        rounds.append(ident)
        if not _apply(ident, rows, gains):
            break
        cmds = tuple((CMD_PID, a * 3 + g, float(v)) for a, kpid in sorted(gains.items()) for g, v in enumerate(kpid))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(cmds=cmds, rounds=rounds), indent=1, default=float))
    return cmds, rounds
