"""Synthetic excite-flight log for the autotune tests and a cli demo (not flight data).

One axis of the cascade at the firmware rates: angle PID -> rate PID (API/pid.c form and clamps, 200 Hz) ->
known plant k e^{-sT} / (s (tau s + 1)) integrated at 1 ms, with a torque disturbance, gyro noise, a wandering
angle setpoint (the position loop) and the SysID dither added to the rate setpoint (StabilizerTask.c:1337-1340).
The log is a session telemetry.csv (received_ns,slot,key,value) with the core symbols the cli reads, at 100 Hz
with host jitter; id_frame=True adds the 0x03 ID frame keys at 200 Hz during the run.

    python -m ground_station.autotune.synth <out_dir> [--axis roll] [--amp 60] [--noise 2]
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from ground_station.autotune import excitation as ex
from ground_station.autotune.design import Plant, PidRow, read_pid_rows
from ground_station.autotune.frf import ANGLE_PID, RATE_PID

DEMO_PLANT = Plant(k=8.5, tau_s=0.03, delay_s=0.01)  # made up for the demo, near research/sim/cascade.py tests


class _Pid:
    """ComputePID with the EMin integral gate and the clamps of one PID_ROW."""

    def __init__(self, row: PidRow) -> None:
        self.row, self.sume, self.pre_e = row, 0.0, 0.0

    def __call__(self, e: float) -> float:
        r = self.row
        if abs(e) < r.emin:
            self.sume = min(max(self.sume + e, -r.sumemax), r.sumemax)
        u = (min(max(r.kp * e, -r.upmax), r.upmax) + min(max(r.ki * self.sume, -r.uimax), r.uimax)
             + min(max(r.kd * (e - self.pre_e), -r.udmax), r.udmax))
        self.pre_e = e
        return min(max(u, -r.umax), r.umax)


def simulate(axis: str = "roll", plant: Plant = DEMO_PLANT, rate: PidRow | None = None, angle: PidRow | None = None,
             excite: Mapping[str, Any] = ex.ID_EXCITE, pre_s: float = 8.0, post_s: float = 5.0,
             gyro_noise_dps: float = 2.0, dist: float = 15.0, seed: int = 0, id_frame: bool = False) -> pd.DataFrame:
    """Long-format rows of one flight: climb 1 s, hover pre_s, excite, recovery, hover post_s."""
    rows = read_pid_rows()
    rate = rate or rows[RATE_PID[axis]]
    angle = angle or rows[ANGLE_PID[axis]]
    rng = np.random.default_rng(seed)
    t_ex = 1.0 + pre_s
    n = int(round((t_ex + ex.step_s(excite["duration_s"]) + post_s) / ex.DT_S))
    sub, h = 5, ex.DT_S / 5
    nd = int(round(plant.delay_s / h))
    buf, j = np.zeros(nd + 1), 0
    a = 1.0 - math.exp(-h / plant.tau_s)
    t = np.arange(n) * ex.DT_S
    d = ex.dither(t - t_ex, excite["signal"], excite["f0"], excite["f1"], excite["amp"], excite["duration_s"])
    pid_r, pid_a = _Pid(rate), _Pid(angle)
    v = w = ang = tq = ang_sp = 0.0
    log = np.zeros((n, 3))  # Des, FB, angle
    for i in range(n):
        tq += -tq * ex.DT_S / 0.3 + dist * math.sqrt(2 * ex.DT_S / 0.3) * rng.standard_normal()
        ang_sp += -ang_sp * ex.DT_S / 2.0 + 1.0 * math.sqrt(2 * ex.DT_S / 2.0) * rng.standard_normal()
        fb = w + gyro_noise_dps * rng.standard_normal()
        des = pid_a(ang_sp - ang) + d[i]
        u = pid_r(des - fb)
        for _ in range(sub):
            buf[j] = u + tq
            j = (j + 1) % (nd + 1)
            v += a * (buf[j] - v)
            w += plant.k * v * h
            ang += w * h
        log[i] = des, fb, ang
    return _rows(axis, t, log, t_ex, excite, rng, id_frame, d)


def _rows(axis: str, t: np.ndarray, log: np.ndarray, t_ex: float, excite: Mapping[str, Any],
          rng: np.random.Generator, id_frame: bool, d: np.ndarray) -> pd.DataFrame:
    pid = RATE_PID[axis]
    k = np.arange(0, len(t), 2)  # 100 Hz core streams
    host_ns = (t[k] * 1e9 + np.maximum.accumulate(rng.uniform(0, 20e6, len(k)))).astype(np.int64)
    prim = np.where(t[k] < 1.0, 1.0, 2.0)
    cols = {"xTickCount": np.round(t[k] * 1000.0), f"Ctrler.{pid}.Des": log[k, 0], f"Ctrler.{pid}.FB": log[k, 1],
            "g_wfb_status.prim_state": prim}
    frames = [pd.DataFrame({"received_ns": host_ns, "slot": 0, "key": f"slot0.{key}", "value": val})
              for key, val in cols.items()]
    if id_frame:
        on = (t >= t_ex) & (t < t_ex + ex.step_s(excite["duration_s"]))
        rel = t[on] - t_ex
        state = np.select([rel < ex.RAMP_T_S, rel < ex.RAMP_T_S + excite["duration_s"],
                           rel < ex.active_s(excite["duration_s"])],
                          [ex.STATE_RAMP_IN, ex.STATE_RUNNING, ex.STATE_RAMP_OUT], ex.STATE_RECOVERY)
        ns = (t[on] * 1e9).astype(np.int64) + 3_000_000
        idc = {"id.sample_counter": np.arange(on.sum()), f"id.{axis}.r": log[on, 0] / 57.29578,
               f"id.{axis}.x": log[on, 1] / 57.29578, "id.dither": d[on], "id.sysid_state": state}
        frames += [pd.DataFrame({"received_ns": ns, "slot": 1, "key": f"slot1.{key}", "value": val})
                   for key, val in idc.items()]
    return pd.concat(frames).sort_values("received_ns", kind="stable")


def write_session(out_dir: str | Path, rows: pd.DataFrame) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rows.to_csv(out / "telemetry.csv", index=False)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out_dir")
    ap.add_argument("--axis", choices=sorted(RATE_PID), default="roll")
    ap.add_argument("--amp", type=float, default=float(ex.ID_EXCITE["amp"]))
    ap.add_argument("--noise", type=float, default=2.0, help="gyro noise deg/s rms")
    args = ap.parse_args(argv)
    path = write_session(args.out_dir, simulate(args.axis, excite={**ex.ID_EXCITE, "amp": args.amp},
                                                gyro_noise_dps=args.noise))
    print(f"wrote {path / 'telemetry.csv'} (synthetic, plant {DEMO_PLANT})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
