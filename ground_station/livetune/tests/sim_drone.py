"""SimDrone: roll and pitch angle + rate loops for the live-tune closed-loop tests (no hardware).

PID rows: cascade_rank.cand_rows("F1x+F3+F5w+F6a"), the API/pid.c rows test_firmware_rows checks. Rate plants:
cascade_rank.sysid_plant() (docs/sysid_results.md: gain, motor lag, transport delay). Scalar ComputePID AW_LEGACY as in
cascade.Pid, 200 Hz. No position loops: the drone holds level at the hover point; pos_xy is settable.
CMD 0x14 mimics API/sysid.c: params idx 0-5, idx 6 start (origin reset, refused unless IDLE) or abort, cosine ramp
1.5 s, log chirp (signal 0) or peak-normalised Schroeder multisine (signal 1) on the rate setpoint, ramp 1.5 s,
RECOVERY 2.0 s. The multisine is normalised over min(dur, 8) s as the firmware does (API/sysid.c:209).
Fault hooks: link_up = False (commands fail, samples age), kick(axis, dps) (rate spike), prim_state (takeover).
"""

from __future__ import annotations

import math

import numpy as np

from ground_station.research.sim import cascade as c
from ground_station.research.sim.cascade_rank import cand_rows, sysid_plant
from ground_station.service.abort_monitor import AbortSample

TICK = c.TICK
AXES = ("roll", "pitch")
PID_AXIS = {0: ("pitch", "ang"), 1: ("roll", "ang"), 3: ("roll", "rate"), 4: ("pitch", "rate")}  # CMD 0x01 axis
SYSID_AXIS = {0: "pitch", 1: "roll"}
RAMP_S, RECOVERY_S, MS_K = 1.5, 2.0, 20


class _Pid:
    def __init__(self, row: c.PidRow) -> None:
        for f in c.ROW_FIELDS:
            setattr(self, f, float(getattr(row, f)))
        self.SumE = self.PreE = self.U = 0.0

    def step(self, des: float, fb: float) -> float:
        e = des - fb
        if ((self.U <= self.UMax and e > 0) or (self.U >= -self.UMax and e < 0)) and abs(e) < self.EMin:
            self.SumE = min(max(self.SumE + e, -self.SumEMax), self.SumEMax)
        ui = min(max(self.Ki * self.SumE, -self.UiMax), self.UiMax)
        up = min(max(self.Kp * e, -self.UpMax), self.UpMax)
        ud = min(max(self.Kd * (e - self.PreE), -self.UdMax), self.UdMax)
        self.U = min(max(up + ui + ud, -self.UMax), self.UMax)
        self.PreE = e
        return self.U


class _Axis:
    def __init__(self, plant: dict, rows: dict) -> None:
        self.k = plant["k"]
        self.a_m = 1.0 - math.exp(-TICK / plant["tau_m"])
        self.ubuf = [0.0] * (int(round(plant["delay"] / TICK)) + 1)
        self.ang, self.rate = _Pid(rows["ang"]), _Pid(rows["rate"])
        self.th = self.w = self.tm = 0.0
        self.w_meas = self.des = 0.0


class SimDrone:
    def __init__(self, seed: int = 0, gyro_noise_dps: float = 2.0, hover_z: float = 0.8) -> None:
        rows, plants = cand_rows("F1x+F3+F5w+F6a"), sysid_plant()
        self.ax = {a: _Axis(plants[a], rows) for a in AXES}
        self.rng = np.random.default_rng(seed)
        self.noise = gyro_noise_dps
        self.hover_z = hover_z
        self.t = 0.0
        self.link_up = True
        self._last_rx = 0.0
        self.prim_state = 2
        self.pos_xy = [0.0, 0.0]
        self.flags: dict[int, float] = {}
        self.writes: list[tuple[float, int, int, float]] = []    # applied
        self.attempts: list[tuple[float, int, int, float]] = []  # every send, link up or not
        self.origin_resets = 0
        self.sysid_starts = 0
        self._sx = {0: 1.0, 1: 1.0, 2: 1.0, 3: 12.0, 4: 30.0, 5: 20.0}
        self._sid_state = "idle"
        self._sid_t = 0.0
        self._sid_axis = "roll"

    # ---------------------------------------------------------------- link

    def send(self, cmd: int, idx: int, val: float) -> bool:
        self.attempts.append((self.t, cmd, idx, val))
        if not self.link_up:
            return False
        self.writes.append((self.t, cmd, idx, val))
        if cmd == 0x01:
            axis, gain = divmod(idx, 3)
            if axis in PID_AXIS and 0.0 <= val <= 200.0:
                name, loop = PID_AXIS[axis]
                setattr(getattr(self.ax[name], loop), ("Kp", "Ki", "Kd")[gain], float(val))
        elif cmd == 0x0F:
            self.flags[idx] = val
        elif cmd == 0x14:
            if idx in self._sx:
                self._sx[idx] = val
            elif idx == 6 and val >= 0.5:
                self.origin_resets += 1
                self.pos_xy = [0.0, 0.0]
                if self._sid_state == "idle":
                    self._sid_start()
            elif idx == 6 and self._sid_state in ("run",):
                self._sid_state, self._sid_t = "recovery", 0.0
        return True

    def rate_gains(self, axis: str = "roll") -> dict[str, float]:
        r = self.ax[axis].rate
        return {"Kp": r.Kp, "Ki": r.Ki, "Kd": r.Kd}

    def kick(self, axis: str, dps: float) -> None:
        self.ax[axis].w += dps

    # ---------------------------------------------------------------- SysID

    def _sid_start(self) -> None:
        f0, f1, self._sid_amp, self._sid_dur = self._sx[2], self._sx[3], self._sx[4], max(1.0, self._sx[5])
        k = np.arange(MS_K)
        self._ms_f = f0 * (f1 / f0) ** (k / (MS_K - 1))
        self._ms_phi = -math.pi * k * k / MS_K
        self._ms_w = self._ms_f / f0
        tt = np.arange(0.0, min(self._sid_dur, 8.0), 0.002)
        self._ms_norm = 1.05 * np.max(np.abs(np.sin(2 * np.pi * np.outer(tt, self._ms_f) + self._ms_phi) @ self._ms_w))
        self._sid_axis = SYSID_AXIS.get(int(round(self._sx[0])), "roll")
        self._sid_state, self._sid_t, self._phase = "run", 0.0, 0.0
        self.sysid_starts += 1

    def _sid_cmd(self) -> float:
        if self._sid_state == "recovery":
            self._sid_t += TICK
            if self._sid_t >= RECOVERY_S:
                self._sid_state = "idle"
            return 0.0
        if self._sid_state != "run":
            return 0.0
        self._sid_t += TICK
        t, run_end = self._sid_t, RAMP_S + self._sid_dur
        if t < RAMP_S:
            env = 0.5 * (1.0 - math.cos(math.pi * t / RAMP_S))
        elif t < run_end:
            env = 1.0
        elif t < run_end + RAMP_S:
            env = 0.5 * (1.0 + math.cos(math.pi * (t - run_end) / RAMP_S))
        else:
            self._sid_state, self._sid_t = "recovery", 0.0
            return 0.0
        if int(round(self._sx[1])) == 1:
            raw = float(np.sin(2 * np.pi * self._ms_f * t + self._ms_phi) @ self._ms_w) / self._ms_norm
        else:  # log chirp: f0 in ramp-in, sweep f0 -> f1 over the run, f1 in ramp-out (sysid.c:126-133)
            tau = min(max((t - RAMP_S) / self._sid_dur, 0.0), 1.0)
            self._phase += 2 * math.pi * self._sx[2] * (self._sx[3] / self._sx[2]) ** tau * TICK
            raw = math.sin(self._phase)
        return env * self._sid_amp * raw

    # ---------------------------------------------------------------- dynamics

    def step(self, dt: float) -> None:
        for _ in range(max(1, int(round(dt / TICK)))):
            inj = self._sid_cmd()
            for name, a in self.ax.items():
                a.w_meas = a.w + self.noise * float(self.rng.standard_normal())
                a.des = a.ang.step(0.0, a.th) + (inj if name == self._sid_axis else 0.0)
                u = a.rate.step(a.des, a.w_meas)
                a.ubuf.append(u)
                a.tm += (a.ubuf.pop(0) - a.tm) * a.a_m
                a.w += TICK * a.k * a.tm
                a.th += TICK * a.w
            self.t += TICK
            if self.link_up:
                self._last_rx = self.t

    def clock(self) -> float:
        return self.t

    def status(self) -> dict[str, float]:
        return {"prim_state": self.prim_state}

    def sample(self, t_s: float | None = None) -> AbortSample:
        r, p = self.ax["roll"], self.ax["pitch"]
        sat = sum(0.25 for a in (r, p) if abs(a.rate.U) >= a.rate.UMax * 0.999)
        return AbortSample(
            t_s=self.t if t_s is None else t_s, age_s=self.t - self._last_rx + 0.01, airborne=True,
            pos_m=(self.pos_xy[0], self.pos_xy[1], self.hover_z), ref_m=(0.0, 0.0, self.hover_z),
            roll_deg=r.th, pitch_deg=p.th, rate_err_dps=(r.w_meas - r.des, p.w_meas - p.des, 0.0), sat_frac=sat,
        )
