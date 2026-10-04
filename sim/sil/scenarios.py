"""SIL scenarios: one trajectory plus any number of disturbances, composed with '+' (e.g. "figure8+wind_gust+cog").

Times are scenario times; the run adds WARMUP seconds of hover before t = 0 (the firmware integrators fill as after
a takeoff) and scores only t >= 0. Static disturbances (mass, CoG, payloads, delay, noise) are on from the start.
Every row's nominal randomness (motor mismatch, biases, yaw imbalance, battery start, noise) depends on the seed
only, so all controllers and all scenarios of a seed share it (common random numbers).

References are in the firmware world frame (TWC.target_x/y, the ground station's x/y; fw_x = -plant_y,
fw_y = plant_x, plant.py). Sources: nominal ranges from sim/bench/scen.py:86-92; experiments H/D/F and the figure-8
from docs/analysis/controller-roadmap-2026-10-03.md:183-187 and ground_station/service/trajectory_pipeline.py:95-122.
Every other number below is PROPOSED.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from sim.sil.plant import DT_C, G, J_SCALE, J_SIL, bp

WARMUP = 8.0           # PROPOSED: s of hover before t = 0 (Z_ratePID Ui carries the 3050 -> hover PWM gap)
Z_HOVER = 0.8          # roadmap: every experiment flies at z 0.8
V_PATH = 0.3           # roadmap F: 0.3 m/s


@dataclass(frozen=True)
class Traj:
    name: str
    doc: str
    duration: float
    path: Callable[[np.ndarray], tuple[np.ndarray, np.ndarray, np.ndarray]]   # t -> x, y (m, fw frame), ff on


@dataclass(frozen=True)
class Disturbance:
    name: str
    doc: str
    apply: Callable[[dict], None]           # edits the plant parameter dict in place (times are sim times)
    z: float | None = None                  # hover height override
    min_duration: float = 0.0


@dataclass(frozen=True)
class Scenario:
    traj: Traj
    dists: tuple[Disturbance, ...] = field(default_factory=tuple)
    z_set: float | None = None              # hover height override (log replay)

    @property
    def name(self) -> str:
        return "+".join([self.traj.name] + [d.name for d in self.dists])

    @property
    def duration(self) -> float:
        return max([self.traj.duration] + [d.min_duration for d in self.dists])

    @property
    def z(self) -> float:
        zs = [d.z for d in self.dists if d.z is not None]
        return self.z_set if self.z_set is not None else min(zs) if zs else Z_HOVER

    def n_ticks(self) -> int:
        return int(round((WARMUP + self.duration) / DT_C))

    def reference(self) -> tuple[np.ndarray, np.ndarray]:
        """(p_ref (N, 3) fw frame m, ff (N,) bool) at 200 Hz, warmup included."""
        t = np.arange(self.n_ticks()) * DT_C - WARMUP
        x, y, ff = self.traj.path(np.maximum(t, 0.0))
        x, y, ff = np.where(t < 0, 0.0, x), np.where(t < 0, 0.0, y), np.where(t < 0, False, ff)
        return np.stack([x, y, np.full_like(t, self.z)], 1), ff

    def plant_params(self, seed: int) -> dict:
        r = np.random.default_rng([seed, 31])
        q = dict(seed=[seed, 31, 1], noise_scale=1.0, mass=bp.MASS, J=J_SIL.copy(), V0=r.uniform(15.0, 16.6),
                 vsag=0.01, mgain=1 + 0.03 * r.standard_normal(4), u_imb=r.uniform(350, 500),
                 gyro_bias=0.3 * r.standard_normal(3), acc_bias=0.05 * r.standard_normal(3),
                 of_delay=8, of_scale=0.03 * r.standard_normal(),
                 mloss_t=1e9, mloss_idx=0, mloss_eff=1.0, dryden_sigma=0.0, wind=np.zeros(3), wind_t0=0.0,
                 gust_t=np.full(2, 1e9), gust_dur=np.ones(2), gust_vec=np.zeros((2, 3)), cog=np.zeros(2),
                 extra_delay=0, thrust_drift=0.0, drift_t0=WARMUP, drift_T=90.0,
                 pend_m=0.0, pend_L=0.3, pend_kick=0.0, pend_kick_t=1e9, hook=np.array([0.0, 0.0, -0.05]))
        for d in self.dists:
            d.apply(q)
        return q


# ---- trajectories ----
def _hold(t):
    return np.zeros_like(t), np.zeros_like(t), np.zeros_like(t, bool)


def _minjerk(t, t0, a, b, T):
    s = np.clip((t - t0) / T, 0.0, 1.0)
    return a + (b - a) * s ** 3 * (10 - 15 * s + 6 * s * s)


def _doublet(t):
    """Roadmap D: goto x +0.4 / -0.4 / 0 (dwell 3 s), then the same in y. Each goto is a min-jerk segment with peak
    speed V_PATH (T = 1.875 d / v), flown as a trajectory (TrajFF on). PROPOSED: a 2 s hold first."""
    x, y, ff = np.zeros_like(t), np.zeros_like(t), np.zeros_like(t, bool)
    t0 = 2.0
    for axis in (0, 1):
        pos = 0.0
        for tgt in (0.4, -0.4, 0.0):
            T = 1.875 * abs(tgt - pos) / V_PATH
            seg = _minjerk(t, t0, pos, tgt, T)
            m = t >= t0
            (x if axis == 0 else y)[m] = seg[m]
            ff |= (t >= t0) & (t < t0 + T)
            pos, t0 = tgt, t0 + T + 3.0
    return x, y, ff


DOUBLET_T = 2.0 + 2 * (3 * 3.0 + 1.875 * (0.4 + 0.8 + 0.4) / V_PATH)


def _fig8_lookup(width=1.0, height=0.5, n=20000):
    th = np.linspace(0, 2 * np.pi, n)
    x, y = width / 2 * np.sin(2 * th), height / 2 * np.sin(th)       # trajectory_pipeline._shape_figure8
    s = np.r_[0, np.cumsum(np.hypot(np.diff(x), np.diff(y)))]
    return th, s


_FIG8_TH, _FIG8_S = _fig8_lookup()
FIG8_RAMP, FIG8_HOLD0, FIG8_LAPS = 1.0, 2.0, 2   # PROPOSED: 1 s speed ramps, 2 s hold first
FIG8_T = FIG8_HOLD0 + FIG8_LAPS * _FIG8_S[-1] / V_PATH + FIG8_RAMP + 3.0


def _figure8(t):
    """Roadmap F: figure8 width 1.0 height 0.5 at 0.3 m/s, 2 laps, constant speed along the path."""
    T_move = FIG8_LAPS * _FIG8_S[-1] / V_PATH + FIG8_RAMP             # ramp in and out keep the mean speed
    r = FIG8_RAMP
    tau_raw = t - FIG8_HOLD0
    tau = np.clip(tau_raw, 0.0, T_move)
    arc = np.where(tau < r, V_PATH * tau ** 2 / (2 * r), V_PATH * (tau - r / 2))
    arc = np.where(tau > T_move - r, V_PATH * (T_move - r) - V_PATH * (T_move - tau) ** 2 / (2 * r), arc)
    arc = np.clip(arc, 0.0, FIG8_LAPS * _FIG8_S[-1])
    th = np.interp(arc % _FIG8_S[-1], _FIG8_S, _FIG8_TH)
    th = np.where(arc >= FIG8_LAPS * _FIG8_S[-1] - 1e-9, 0.0, th)
    return 0.5 * np.sin(2 * th), 0.25 * np.sin(th), (tau_raw >= 0) & (tau_raw <= T_move)


TRAJS = {t.name: t for t in (
    Traj("hover", "roadmap H: hold at z 0.8 for 40 s", 40.0, _hold),
    Traj("doublet", "roadmap D: +-0.4 m in x then y, 3 s dwell", DOUBLET_T, _doublet),
    Traj("figure8", "roadmap F: 1.0 x 0.5 m figure-8 at 0.3 m/s, 2 laps", FIG8_T, _figure8),
)}


# ---- disturbances (PROPOSED values, sim times = scenario time + WARMUP) ----
def _set(**kv):
    def f(q):
        for k, v in kv.items():
            q[k] = v
    return f


def _wind_step(q):
    q["wind"] = np.array([0.0, 2.0, 0.0])
    q["wind_t0"] = WARMUP + 5.0


def _wind_gust(q):
    q["wind"] = np.array([0.7, 0.7, 0.0])
    q["dryden_sigma"] = 0.5
    q["gust_t"] = WARMUP + np.array([8.0, 20.0])
    q["gust_dur"] = np.array([1.0, 1.0])
    q["gust_vec"] = np.array([[0.0, 2.5, 0.0], [2.5, 0.0, 0.0]])


def _offset_mass(q, m_add, r):
    """Rigid point mass m_add at body offset r (m): total mass, CoG shift and inertia (scaled like the plant's J)."""
    m0 = q["mass"]
    q["mass"] = m0 + m_add
    q["cog"] = q["cog"] + m_add * np.asarray(r[:2]) / q["mass"]
    rx, ry, rz = r
    dJ = m_add * np.array([ry * ry + rz * rz, rx * rx + rz * rz, rx * rx + ry * ry])
    q["J"] = q["J"] + dJ * J_SCALE


def _mass(k):
    def f(q):
        q["mass"] = q["mass"] * k
        q["J"] = q["J"] * (1 + 0.5 * (k - 1))                  # scen.py:106 payload rule
    return f


def _pendulum(q):
    q["pend_m"] = 0.1
    q["pend_L"] = 0.3
    q["pend_kick"] = math.sqrt(2 * G * 0.3 * (1 - math.cos(math.radians(15.0))))   # a 15 deg swing
    q["pend_kick_t"] = WARMUP


def _motor80(q):
    q["mloss_t"] = WARMUP + 10.0
    q["mloss_idx"] = 0
    q["mloss_eff"] = 0.8


DISTS = {d.name: d for d in (
    Disturbance("wind_step", "2 m/s steady wind (indoor fan) switched on at t = 5 s", _wind_step),
    Disturbance("wind_gust", "0.7,0.7 m/s mean + Dryden sigma 0.5 m/s + 2.5 m/s 1 s gusts at 8 s and 20 s", _wind_gust),
    Disturbance("ground_effect", "hover at 0.25 m (plant.py ground-effect law)", lambda q: None, z=0.25),
    Disturbance("cog", "CoG offset 15 mm diagonal", _set(cog=np.array([0.0106, 0.0106]))),
    Disturbance("payload100", "100 g rigid payload 0.10 m off-centre (plant +y)",
                lambda q: _offset_mass(q, 0.1, (0.0, 0.10, -0.03))),
    Disturbance("pendulum", "100 g on a 0.3 m cable 5 cm below the CoG, kicked to a 15 deg swing at t = 0", _pendulum),
    Disturbance("mass_p15", "mass +15 % (J +7.5 %)", _mass(1.15)),
    Disturbance("mass_m15", "mass -15 % (J -7.5 %)", _mass(0.85)),
    Disturbance("motor80", "motor 1 thrust x0.8 from t = 10 s", _motor80),
    Disturbance("delay5", "command delay +5 ms (+1 tick on the 15 ms of plant.py)", _set(extra_delay=1)),
    Disturbance("delay10", "command delay +10 ms (+2 ticks)", _set(extra_delay=2)),
    Disturbance("battery", "thrust gain -15 % linear over 90 s (run 90 s)", _set(thrust_drift=0.15), min_duration=90.0),
    Disturbance("noise2", "sensor noise x2 (gyro, accel, OF, ToF)", _set(noise_scale=2.0)),
)}

PAIR_SET = ("wind_gust", "cog", "pendulum", "motor80", "delay10")
WORST = ("wind_gust", "cog", "pendulum", "motor80", "delay10", "noise2")


def parse(spec: str) -> Scenario:
    """'figure8+wind_gust+cog' -> Scenario. No trajectory name = hover."""
    traj, dists = TRAJS["hover"], []
    for part in [p for p in spec.split("+") if p]:
        if part in TRAJS:
            traj = TRAJS[part]
        elif part in DISTS:
            dists.append(DISTS[part])
        else:
            raise KeyError(f"unknown scenario part {part!r}; trajectories {sorted(TRAJS)}, disturbances {sorted(DISTS)}")
    return Scenario(traj, tuple(dists))


def matrix() -> dict[str, list[Scenario]]:
    """Scenario groups of the matrix: nominal, single disturbances (hover), pairs (figure-8), worst stack."""
    singles = [parse(n) for n in DISTS]
    pairs = [parse("+".join(("figure8", a, b))) for i, a in enumerate(PAIR_SET) for b in PAIR_SET[i + 1:]]
    return {"nominal": [parse(n) for n in TRAJS], "single": singles, "pairs": pairs,
            "worst": [parse("+".join(("figure8",) + WORST))]}
